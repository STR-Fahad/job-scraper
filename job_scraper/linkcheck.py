"""Link check: find postings that have been taken down so dead jobs leave the lists.

Plain HTTP GETs of each job's own public posting page (the stored `source_url`), through the
project's `HttpClient` (host pacing, Retry-After, bounded retries, `robots.txt`). No paid or
external API is used and nothing is deleted: a removed posting is *closed* through the existing
lifecycle (`job_sources.lifecycle_state` plus a `lifecycle_events` row), so history is kept and a
later feed observation can reopen it.

Evidence rules (strongest only, never a guess):
  * REMOVED  - HTTP 404 or 410; or a redirect to a generic board page (a parent path of the posting
               on the same host, or a Greenhouse `?error=true` page); or the source's own closed
               marker phrase on an HTTP 200 page.
  * LIVE     - an HTTP 2xx page without a closed marker.
  * UNKNOWN  - everything else: timeouts, transport errors, 429/5xx, 401/403, a redirect to another
               host or to an unrelated page, a disallowing or unreadable robots.txt, a bad URL.
A job source is closed only when two checks on different days both say REMOVED (a LIVE check in
between starts the count again; UNKNOWN is ignored). Page text is untrusted data: it is only
compared with fixed phrases and never followed or executed.

`check_links` is a dry run unless `apply=True`: a dry run still fetches pages but writes nothing,
so it can run while another program holds the database writer lock.
"""

import ipaddress
import json
import logging
import sqlite3
import time
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from .http import HttpClient, Probe

logger = logging.getLogger(__name__)

USER_AGENT = "personal-job-scraper"
LIVE, REMOVED, UNKNOWN = "live", "removed", "unknown"
MIN_GAP = timedelta(hours=12)  # never re-check a source sooner than this
MAX_HOPS = 3
PRIORITY_TRIES = 15  # tries (2 s apart) to open the store for the priority order
REDIRECTS = {301, 302, 303, 307, 308}

# Fixed lowercase phrases a source shows on a page for a job that is no longer open. They are
# matched only on an HTTP 200 page. They come from the sources' usual wording and have NOT been
# verified against live closed pages; HTTP 404/410 and board redirects are the main evidence.
COMMON_MARKERS = (
    "this job is no longer available",
    "this job has expired",
    "this job has been closed",
    "this position is no longer available",
    "this position has been filled",
)
MARKERS = {
    "greenhouse": ("the job you are looking for is no longer open",),
    "ashby": ("job not found",),
    "lever": ("sorry, we couldn't find anything here",),
}


@dataclass(frozen=True)
class Check:
    verdict: str
    reason: str
    evidence: dict = field(default_factory=dict)


def usable_url(url) -> bool:
    """http(s) only, and never a local or private address (stored URLs are untrusted data)."""
    try:
        parts = urlsplit(url or "")
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    if parts.scheme not in {"http", "https"} or not host or host == "localhost":
        return False
    if host.endswith((".local", ".internal", ".localhost")):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "." in host
    return address.is_global


def _segments(path) -> list[str]:
    return [part for part in path.split("/") if part]


def same_posting(original, target, source_id) -> bool:
    """A redirect that only normalises the posting URL (scheme, slash, query, id kept)."""
    o, t = urlsplit(original), urlsplit(target)
    if t.netloc.lower() != o.netloc.lower():
        return False
    if _segments(t.path) == _segments(o.path):
        return True
    return bool(source_id) and str(source_id) in t.path


def generic_board_redirect(original, target) -> bool:
    """The posting redirected to its board or listing page (or the site's error page)."""
    o, t = urlsplit(original), urlsplit(target)
    if t.netloc.lower() != o.netloc.lower():
        return False
    if "error=true" in t.query.lower():
        return True
    original_path, target_path = _segments(o.path), _segments(t.path)
    return (
        len(target_path) < len(original_path) and original_path[: len(target_path)] == target_path
    )


def classify(source, source_id, url, probe: Probe) -> Check:
    """Judge one response. Pure: no network and no database."""
    status = probe.status
    base = {"http_status": status, "url": url}
    if status is None:
        return Check(UNKNOWN, probe.error or "no_response", {**base, "error": probe.error})
    if status in {404, 410}:
        return Check(REMOVED, f"http_{status}", base)
    if status in REDIRECTS:
        if not probe.location:
            return Check(UNKNOWN, "redirect_without_location", base)
        target = urljoin(url, probe.location)
        if generic_board_redirect(url, target):
            return Check(REMOVED, "redirect_to_board_page", {**base, "location": target})
        return Check(UNKNOWN, "redirect_elsewhere", {**base, "location": target})
    if 200 <= status < 300:
        text = probe.text.lower()
        for phrase in COMMON_MARKERS + MARKERS.get(source, ()):
            if phrase in text:
                return Check(REMOVED, "closed_marker", {**base, "marker": phrase})
        return Check(LIVE, "page_available", base)
    return Check(UNKNOWN, f"http_{status}", base)


class Robots:
    """Per-host robots.txt, fetched once through the same HttpClient."""

    def __init__(self, http: HttpClient, interval):
        self.http, self.interval, self.cache = http, interval, {}

    def allows(self, url) -> bool | None:
        """True/False, or None when robots.txt could not be read (the page is then not checked)."""
        parts = urlsplit(url)
        key = (parts.scheme, parts.netloc)
        if key not in self.cache:
            probe = self.http.probe(
                f"{parts.scheme}://{parts.netloc}/robots.txt",
                headers={"Accept": "text/plain"},
                interval=self.interval,
            )
            if probe.status is not None and 200 <= probe.status < 300:
                parser = RobotFileParser()
                parser.parse(probe.text.splitlines())
                self.cache[key] = parser
            elif probe.status is not None and 400 <= probe.status < 500:
                self.cache[key] = True  # no robots.txt: nothing is disallowed
            else:
                self.cache[key] = None
        entry = self.cache[key]
        if entry is None:
            return None
        return True if entry is True else entry.can_fetch(USER_AGENT, url)


def check_one(http, robots, source, source_id, url, interval) -> Check:
    """Fetch one posting (following only same-posting redirects) and classify it."""
    if not usable_url(url):
        return Check(UNKNOWN, "unusable_url", {"url": url})
    allowed = robots.allows(url)
    if allowed is None:
        return Check(UNKNOWN, "robots_unreadable", {"url": url})
    if not allowed:
        return Check(UNKNOWN, "disallowed_by_robots", {"url": url})
    current = url
    for _ in range(MAX_HOPS + 1):
        probe = http.probe(current, interval=interval, headers={"Accept": "text/html"})
        if probe.status in REDIRECTS and probe.location:
            target = urljoin(current, probe.location)
            if same_posting(current, target, source_id) and usable_url(target):
                current = target
                continue
        result = classify(source, source_id, current, probe)
        if current != url:
            result.evidence["requested_url"] = url
        return result
    return Check(UNKNOWN, "too_many_redirects", {"url": url})


def decide_close(history, new_verdict, now) -> list[dict] | None:
    """Return the two removal checks that justify closing, or None.

    `history` is the source's earlier link-check observations (oldest first, each a dict with
    `verdict` and `checked_at`). UNKNOWN observations are ignored; a LIVE one resets the count.
    The earlier REMOVED check must be on a different UTC day and at least `MIN_GAP` before now.
    """
    if new_verdict != REMOVED:
        return None
    for event in reversed([h for h in history if h["verdict"] != UNKNOWN]):
        if event["verdict"] != REMOVED:
            return None
        when = datetime.fromisoformat(event["checked_at"])
        if when.date() != now.date() and now - when >= MIN_GAP:
            return [event]
    return None


def _history(db, key) -> list[dict]:
    rows = db.execute(
        "SELECT checked_at, evidence FROM lifecycle_events WHERE source=? AND board=? "
        "AND source_id=? ORDER BY id",
        key,
    ).fetchall()
    out = []
    for checked_at, evidence in rows:
        try:
            data = json.loads(evidence)
        except ValueError:
            continue
        if data.get("kind") == "link_check":
            out.append(
                {
                    "verdict": data.get("verdict"),
                    "checked_at": checked_at,
                    "reason": data.get("reason"),
                }
            )
    return out


def _priorities(db, config, path) -> tuple[dict[int, int], bool]:
    """(tiers, scoped). Tiers: 0 Eligible (includes To review), 1 Unverified, 2 in the pool but not
    judged yet, 3 Ineligible. Jobs missing from the dict are outside the ranking pool (Module 2
    ineligible, not software) and are not worth checking either.

    `scoped` is False when the ranking could not be read, and then nothing is skipped. The ranking
    needs a short, normal open of the store; if another program holds the writer lock (a collection
    or Module 2 run) the order falls back to "judged jobs first" so a dry run still works.
    """
    from . import ranking
    from .storage import Store

    try:
        for attempt in range(PRIORITY_TRIES):  # a judge import or Module 2 run holds it briefly
            try:
                store = Store(path)
                break
            except OSError:
                if attempt == PRIORITY_TRIES - 1:
                    raise
                time.sleep(2)
        with store:
            rows = ranking.build(store, config)
    except OSError:
        try:
            return {
                row[0]: 1 for row in db.execute("SELECT DISTINCT job_id FROM ai_assessments")
            }, False
        except sqlite3.OperationalError:  # no assessments table yet
            return {}, False
    except Exception as exc:  # ordering is a convenience; never block the check on it
        logger.warning("Priority ordering unavailable: %s", exc)
        return {}, False
    tiers = {}
    for row in rows:
        status = (row.get("final") or {}).get("status")
        tiers[row["job_id"]] = (
            0 if status == "ELIGIBLE" else 1 if status == "UNVERIFIED" else 3 if status else 2
        )
    return tiers, True


def _candidates(db, config, path, source, now, include_ineligible=False, min_gap=MIN_GAP):
    rows = db.execute(
        "SELECT source, board, source_id, job_id, lifecycle_state, "
        "json_extract(payload,'$.source_url') AS url, json_extract(payload,'$.title') AS title, "
        "json_extract(payload,'$.company') AS company FROM job_sources "
        "WHERE lifecycle_state IN ('active','unknown')" + (" AND source=?" if source else ""),
        (source,) if source else (),
    ).fetchall()
    last = {}
    for key_source, board, source_id, checked_at, evidence in db.execute(
        "SELECT source, board, source_id, checked_at, evidence FROM lifecycle_events "
        "WHERE evidence LIKE '%\"link_check\"%' ORDER BY id"
    ):
        try:
            if json.loads(evidence).get("kind") == "link_check":
                last[(key_source, board, source_id)] = checked_at
        except ValueError:
            continue
    tiers, scoped = _priorities(db, config, path)
    scoped = scoped and not include_ineligible
    fresh, recent, skipped = [], 0, 0
    for row in rows:
        key = (row["source"], row["board"], row["source_id"])
        tier = tiers.get(row["job_id"])
        if scoped and (tier is None or tier == 3):
            skipped += 1
            continue
        if key in last and now - datetime.fromisoformat(last[key]) < min_gap:
            recent += 1
            continue
        fresh.append((2 if tier is None else tier, row["job_id"], key, row))
    fresh.sort(key=lambda item: (item[0], item[1], item[2]))
    return fresh, recent, skipped


def _backup(store) -> str:
    path = store.db.execute("PRAGMA database_list").fetchone()[2]
    if not path:
        return ""
    folder = Path(path).parent / "backups"
    folder.mkdir(exist_ok=True)
    target = (
        folder / f"{Path(path).stem}-pre-linkcheck-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.sqlite3"
    )
    with closing(sqlite3.connect(target)) as copy:
        store.db.backup(copy)
        if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Link-check backup failed integrity check")
    return str(target)


def _write(store, results, now) -> int:
    """Record every result as a lifecycle event and close the sources that earned it."""
    closed = 0
    with store.db:
        for item in results:
            key, check = item["key"], item["check"]
            stamp = item.get("at", now).isoformat()
            evidence = {
                "kind": "link_check",
                "verdict": check.verdict,
                "reason": check.reason,
                **check.evidence,
            }
            store.db.execute(
                "INSERT INTO lifecycle_events(source,board,source_id,from_state,to_state,"
                "checked_at,evidence,scan_id) VALUES (?,?,?,?,?,?,?,NULL)",
                (*key, item["state"], item["state"], stamp, json.dumps(evidence)),
            )
            store.db.execute(
                "UPDATE job_sources SET last_checked_at=? WHERE source=? AND board=? AND source_id=?",
                (stamp, *key),
            )
            if item["closes"]:
                proof = {
                    "reason": "link_check_removed_twice",
                    "checks": [*item["closes"], {"verdict": check.verdict, "checked_at": stamp}],
                    "last_evidence": evidence,
                }
                store.db.execute(
                    "UPDATE job_sources SET lifecycle_state='closed', closure_evidence=? "
                    "WHERE source=? AND board=? AND source_id=? "
                    "AND lifecycle_state IN ('active','unknown')",
                    (json.dumps(proof), *key),
                )
                store.db.execute(
                    "INSERT INTO lifecycle_events(source,board,source_id,from_state,to_state,"
                    "checked_at,evidence,scan_id) VALUES (?,?,?,?,?,?,?,NULL)",
                    (*key, item["state"], "closed", stamp, json.dumps(proof)),
                )
                store._refresh_job(item["job_id"])
                closed += 1
    return closed


def check_links(
    settings,
    *,
    apply=False,
    limit=100,
    source=None,
    interval=2.0,
    now=None,
    http=None,
    store_factory=None,
    include_ineligible=False,
) -> dict:
    """Check active job sources and report. Writes only with apply=True (after a backup)."""
    from .dashboard.data import connect_readonly
    from .storage import Store

    now = now or datetime.now(UTC)
    owned = None
    if http is None:
        import httpx

        owned = httpx.Client(
            timeout=httpx.Timeout(settings.timeout_seconds),
            follow_redirects=False,
            headers={"User-Agent": f"{USER_AGENT}/0.1"},
        )
        http = HttpClient(owned, retries=settings.retries, max_retry_delay=settings.max_retry_delay)
    results = []
    try:
        with closing(connect_readonly(settings.database)) as db:
            candidates, skipped_recent, skipped_ineligible = _candidates(
                db, settings.matching, settings.database, source, now, include_ineligible
            )
            robots = Robots(http, interval)
            for tier, job_id, key, row in candidates[:limit]:
                check = check_one(http, robots, key[0], key[2], row["url"], interval)
                earlier = _history(db, key)
                closes = decide_close(earlier, check.verdict, now)
                results.append(
                    {
                        "key": key,
                        "job_id": job_id,
                        "tier": tier,
                        "state": row["lifecycle_state"],
                        "title": row["title"],
                        "company": row["company"],
                        "url": row["url"],
                        "check": check,
                        "closes": closes,
                    }
                )
    finally:
        if owned is not None:
            owned.close()
    backup, closed_written = "", 0
    if apply and results:
        factory = store_factory or Store
        for attempt in range(60):  # another program (a collection run) may hold the writer lock
            try:
                store = factory(settings.database)
                break
            except OSError:
                if attempt == 59:
                    raise
                time.sleep(2)
        with store:
            backup = _backup(store)
            closed_written = _write(store, results, now)
    return _report(
        results,
        apply,
        len(candidates),
        skipped_recent,
        skipped_ineligible,
        limit,
        backup,
        closed_written,
    )


def _report(
    results, apply, total, skipped_recent, skipped_ineligible, limit, backup, closed_written
) -> dict:
    by_source = {}
    removed, unknown = [], Counter()
    for item in results:
        row = by_source.setdefault(
            item["key"][0],
            {"checked": 0, "live": 0, "removed_first_check": 0, "closed": 0, "unknown": 0},
        )
        row["checked"] += 1
        verdict = item["check"].verdict
        if verdict == LIVE:
            row["live"] += 1
        elif verdict == UNKNOWN:
            row["unknown"] += 1
            unknown[item["check"].reason] += 1
        else:
            row["closed" if item["closes"] else "removed_first_check"] += 1
            removed.append(
                {
                    "job_id": item["job_id"],
                    "title": item["title"],
                    "company": item["company"],
                    "source": item["key"][0],
                    "url": item["url"],
                    "reason": item["check"].reason,
                    "tier": item["tier"],
                    "action": ("closed" if apply else "would close")
                    if item["closes"]
                    else (
                        "recorded; closes on a second check on another day"
                        if apply
                        else "would record the first removed check"
                    ),
                }
            )
    totals = Counter()
    for row in by_source.values():
        totals.update(row)
    return {
        "mode": "apply" if apply else "dry-run",
        "candidates": total,
        "skipped_checked_within_12h": skipped_recent,
        "skipped_ineligible_or_outside_pool": skipped_ineligible,
        "limit": limit,
        "not_checked_this_run": max(0, total - len(results)),
        "totals": dict(totals),
        "by_source": by_source,
        "removed": removed,
        "unknown_reasons": dict(unknown),
        "closed_written": closed_written,
        "backup": backup,
    }


def render(report) -> str:
    totals = report["totals"]
    lines = [
        f"Link check ({report['mode']}): {totals.get('checked', 0)} checked, "
        f"{totals.get('live', 0)} live, "
        f"{totals.get('removed_first_check', 0)} removed (first check), "
        f"{totals.get('closed', 0)} {'closed' if report['mode'] == 'apply' else 'would close'}, "
        f"{totals.get('unknown', 0)} unknown.",
        f"{report['candidates']} sources to check; {report['skipped_checked_within_12h']} were "
        f"checked within 12 hours and skipped; {report['skipped_ineligible_or_outside_pool']} "
        f"Ineligible or outside the judging pool were not checked; {report['not_checked_this_run']} "
        f"not reached this run (limit {report['limit']}).",
        "",
        "source | checked | live | removed (1st) | closed | unknown",
    ]
    for name, row in sorted(report["by_source"].items()):
        lines.append(
            f"{name} | {row['checked']} | {row['live']} | {row['removed_first_check']} | "
            f"{row['closed']} | {row['unknown']}"
        )
    if report["removed"]:
        lines += ["", "Removed postings:"]
        lines += [
            f"  [{r['job_id']}] {r['title']} - {r['company']} ({r['source']}): "
            f"{r['reason']} -> {r['action']}"
            for r in report["removed"]
        ]
    if report["unknown_reasons"]:
        lines += [
            "",
            "Unknown (not treated as removed): "
            + ", ".join(
                f"{reason} x{count}" for reason, count in sorted(report["unknown_reasons"].items())
            ),
        ]
    if report["backup"]:
        lines.append(f"Backup: {report['backup']}")
    if report["mode"] == "dry-run":
        lines.append("Dry run: nothing was written. Add --apply to record the results.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------
# Exchange with a cloud runner. The database never leaves this computer: `export_links` writes the
# public posting links to a small JSON file, a cloud routine fetches them with `check_export` (this
# module alone plus `http.py`, no database) and writes a results file, and `import_results`
# records those results here with the same evidence and two-checks-on-different-days rules.
# ---------------------------------------------------------------------------------------------

EXPORT_VERSION = 1
EVIDENCE_KEYS = (
    "http_status",
    "url",
    "requested_url",
    "location",
    "marker",
    "error",
)


def export_links(settings, *, source=None, include_ineligible=False, now=None) -> dict:
    """The open job sources worth checking (same selection and order as `check-links`)."""
    from .dashboard.data import connect_readonly

    now = now or datetime.now(UTC)
    with closing(connect_readonly(settings.database)) as db:
        candidates, _, skipped = _candidates(
            db,
            settings.matching,
            settings.database,
            source,
            now,
            include_ineligible,
            min_gap=timedelta(0),
        )
    return {
        "version": EXPORT_VERSION,
        "exported_at": now.isoformat(),
        "skipped_ineligible_or_outside_pool": skipped,
        "items": [
            {"source": key[0], "board": key[1], "source_id": key[2], "url": row["url"]}
            for _, _, key, row in candidates
        ],
    }


def bundle_tool(destination) -> list[str]:
    """Copy the three files the cloud runner needs (no database access) into `destination`."""
    here = Path(__file__).resolve().parent
    target = Path(destination) / "job_scraper"
    target.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in ("__init__.py", "http.py", "linkcheck.py"):
        (target / name).write_bytes((here / name).read_bytes())
        copied.append(name)
    return copied


def check_export(export, results_dir, *, http, now=None, interval=2.0, limit=1000) -> dict:
    """Check the exported links and write one results file. No database is used or needed."""
    now = now or datetime.now(UTC)
    items = export.get("items") if isinstance(export, dict) else None
    if not isinstance(items, list) or export.get("version") != EXPORT_VERSION:
        raise ValueError("Unsupported export file")
    robots = Robots(http, interval)
    results = []
    for item in items[:limit]:
        source, board, source_id, url = (
            str(item.get("source", "")),
            str(item.get("board", "")),
            str(item.get("source_id", "")),
            item.get("url"),
        )
        check = check_one(http, robots, source, source_id, url, interval)
        results.append(
            {
                "source": source,
                "board": board,
                "source_id": source_id,
                "verdict": check.verdict,
                "reason": check.reason,
                "evidence": {k: v for k, v in check.evidence.items() if k in EVIDENCE_KEYS},
            }
        )
    folder = Path(results_dir)
    folder.mkdir(parents=True, exist_ok=True)
    name = folder / f"{now:%Y%m%dT%H%M%SZ}.json"
    document = {"version": EXPORT_VERSION, "checked_at": now.isoformat(), "results": results}
    name.write_text(json.dumps(document, indent=1, ensure_ascii=False), encoding="utf-8")
    counts = Counter(r["verdict"] for r in results)
    return {"file": str(name), "checked": len(results), **counts}


def _clean_result(raw) -> dict | None:
    """Validate one result from a results file (it comes from outside this computer)."""
    if not isinstance(raw, dict):
        return None
    source, board, source_id = raw.get("source"), raw.get("board"), raw.get("source_id")
    verdict, reason = raw.get("verdict"), raw.get("reason")
    if not all(isinstance(v, str) for v in (source, board, source_id, reason)):
        return None
    if verdict not in {LIVE, REMOVED, UNKNOWN} or len(reason) > 100:
        return None
    evidence = raw.get("evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    safe = {
        k: (v if isinstance(v, (int, float, str)) and len(str(v)) <= 500 else str(v)[:500])
        for k, v in evidence.items()
        if k in EVIDENCE_KEYS
    }
    return {
        "key": (source, board, source_id),
        "verdict": verdict,
        "reason": reason,
        "evidence": safe,
    }


def read_results(paths) -> tuple[list[dict], int]:
    """Results from files or folders, oldest first. Returns (results, rejected_entries)."""
    files = []
    for path in map(Path, paths):
        files += sorted(path.glob("*.json")) if path.is_dir() else [path]
    out, rejected = [], 0
    for file in files:
        try:
            document = json.loads(file.read_text(encoding="utf-8"))
            at = datetime.fromisoformat(document["checked_at"])
            entries = document["results"]
            if document.get("version") != EXPORT_VERSION or at.tzinfo is None:
                raise ValueError("bad header")
        except (OSError, ValueError, KeyError, TypeError):
            rejected += 1
            continue
        for raw in entries if isinstance(entries, list) else []:
            cleaned = _clean_result(raw)
            if cleaned is None:
                rejected += 1
            else:
                out.append({**cleaned, "at": at.astimezone(UTC), "file": file.name})
    out.sort(key=lambda r: (r["at"], r["key"]))
    return out, rejected


def import_results(settings, paths, *, apply=False, store_factory=None, now=None) -> dict:
    """Record cloud results here and close sources that earned it (dry run unless apply)."""
    from .dashboard.data import connect_readonly
    from .storage import Store

    now = now or datetime.now(UTC)
    parsed, rejected = read_results(paths)
    summary = Counter()
    items, history, done, closed_keys = [], {}, set(), set()
    with closing(connect_readonly(settings.database)) as db:
        for key_source, board, source_id, checked_at, evidence in db.execute(
            "SELECT source, board, source_id, checked_at, evidence FROM lifecycle_events "
            "WHERE evidence LIKE '%\"link_check\"%' ORDER BY id"
        ):
            try:
                kind = json.loads(evidence).get("kind")
            except ValueError:
                continue
            if kind == "link_check":
                done.add(((key_source, board, source_id), checked_at))
        for result in parsed:
            key, at = result["key"], result["at"]
            if (key, at.isoformat()) in done:
                summary["already_imported"] += 1
                continue
            row = db.execute(
                "SELECT job_id, lifecycle_state FROM job_sources WHERE source=? AND board=? "
                "AND source_id=?",
                key,
            ).fetchone()
            if row is None:
                summary["unknown_job"] += 1
                continue
            if key in closed_keys or row["lifecycle_state"] not in {"active", "unknown"}:
                summary["not_open"] += 1
                continue
            if key not in history:
                history[key] = _history(db, key)
            closes = decide_close(history[key], result["verdict"], at)
            history[key].append(
                {"verdict": result["verdict"], "checked_at": at.isoformat(), "reason": None}
            )
            check = Check(result["verdict"], result["reason"], result["evidence"])
            items.append(
                {
                    "key": key,
                    "job_id": row["job_id"],
                    "state": row["lifecycle_state"],
                    "check": check,
                    "closes": closes,
                    "at": at,
                    "tier": 2,
                    "title": None,
                    "company": None,
                    "url": result["evidence"].get("url"),
                }
            )
            summary[result["verdict"]] += 1
            if closes:
                summary["closed" if apply else "would_close"] += 1
                closed_keys.add(key)
    backup = ""
    if apply and items:
        factory = store_factory or Store
        for attempt in range(60):
            try:
                store = factory(settings.database)
                break
            except OSError:
                if attempt == 59:
                    raise
                time.sleep(2)
        with store:
            backup = _backup(store)
            _write(store, items, now)
    return {
        "mode": "apply" if apply else "dry-run",
        "invalid_files_or_entries": rejected,
        "recorded": len(items),
        "summary": dict(summary),
        "backup": backup,
    }


def render_import(report) -> str:
    s = report["summary"]
    lines = [
        f"Link import ({report['mode']}): {report['recorded']} results "
        f"{'recorded' if report['mode'] == 'apply' else 'to record'}: "
        f"{s.get('live', 0)} live, {s.get('removed', 0)} removed, {s.get('unknown', 0)} unknown; "
        f"{s.get('closed', 0) + s.get('would_close', 0)} "
        f"{'closed' if report['mode'] == 'apply' else 'would close'}.",
        f"Skipped: {s.get('already_imported', 0)} already imported, {s.get('unknown_job', 0)} "
        f"not in this database, {s.get('not_open', 0)} already closed; "
        f"{report['invalid_files_or_entries']} invalid entries or files.",
    ]
    if report["backup"]:
        lines.append(f"Backup: {report['backup']}")
    if report["mode"] == "dry-run":
        lines.append("Dry run: nothing was written. Add --apply to record the results.")
    return "\n".join(lines)


def cloud_main(argv=None) -> int:
    """`python -m job_scraper.linkcheck EXPORT.json RESULTS_DIR` - the cloud runner's entry point."""
    import argparse

    import httpx

    parser = argparse.ArgumentParser(description="Check exported posting links (no database)")
    parser.add_argument("export", type=Path)
    parser.add_argument("results_dir", type=Path)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args(argv)
    export = json.loads(args.export.read_text(encoding="utf-8"))
    with httpx.Client(
        timeout=httpx.Timeout(30),
        follow_redirects=False,
        headers={"User-Agent": f"{USER_AGENT}/0.1"},
    ) as client:
        http = HttpClient(client, retries=2, max_retry_delay=60)
        summary = check_export(
            export, args.results_dir, http=http, interval=max(1.0, args.interval), limit=args.limit
        )
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(cloud_main())
