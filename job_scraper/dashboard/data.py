"""Read model for the local dashboard.

Presentation never recomputes classification, matching, ranking or review rules. It reads
what Modules 1-3 stored, through their own functions, and caches the two expensive results
(`processing.inspect_results` and `ranking.build`) until the stored data changes. Page
requests only read SQLite (read-only connections); the single writer path is `actions`.
"""

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import matching, processing, ranking, reviews, tracking
from ..storage import Store

DIMENSIONS = ("category", "experience", "geography", "restrictions", "workplace")
PAGE_SIZE = 25
SORTS = {
    "discovered": "Discovered (newest first)",
    "updated": "Updated (newest first)",
    "company": "Company (A-Z)",
    "title": "Title (A-Z)",
    "match": "Technical match (current assessments only)",
}


class ReadStore:
    """The part of Store that read-only Module 3 helpers use: a query connection."""

    def __init__(self, db):
        self.db = db


def connect_readonly(path) -> sqlite3.Connection:
    uri = f"file:{Path(path).resolve().as_posix()}?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def signature(db) -> tuple:
    """Cheap fingerprint of everything the cached read model depends on."""

    def one(sql):
        return db.execute(sql).fetchone()[0]

    review_mark = (
        one("SELECT coalesce(max(id),0) FROM eligibility_reviews")
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='eligibility_reviews'").fetchone()
        else 0
    )
    return (
        one("SELECT count(*) FROM jobs"),
        one("SELECT coalesce(max(last_seen),'') FROM job_sources"),
        one("SELECT coalesce(max(id),0) FROM job_classification_history"),
        one("SELECT coalesce(max(id),0) FROM lifecycle_events"),
        one("SELECT coalesce(max(id),0) FROM ai_assessments"),
        one("SELECT count(*) FROM ai_assessments"),
        one("SELECT coalesce(max(version),0) FROM candidate_profiles WHERE approved=1"),
        review_mark,
    )


def _experience_text(classification) -> str:
    """Stated, non-optional experience minimum from Module 2's stored extraction."""
    if not classification:
        return "unknown"
    found = [
        item["minimum_years"]
        for variant in classification.get("variants", [])
        for item in variant.get("experience_requirements", [])
        if not item.get("optional")
    ]
    if not found:
        return "not stated"
    low = max(found)
    months = round(low * 12)
    return f"{months} months" if months < 24 and months % 12 else f"{low:g} years"


@dataclass
class Snapshot:
    signature: tuple
    built_at: float
    rows: list[dict]
    population: list[dict]
    counts: dict
    by_id: dict[int, dict] = field(default_factory=dict)
    population_by_id: dict[int, dict] = field(default_factory=dict)


def _assessment_view(entry) -> dict:
    if entry is None:
        return {"assessment_status": "not_in_population"}
    view = {"assessment_status": entry["assessment_status"], "in_population": True}
    technical = entry.get("technical")
    if technical:
        view.update(
            score=technical["score"],
            confidence=technical["confidence_level"],
            coverage=technical["evidence_coverage"],
            ceiling=technical["score_ceiling"],
            assessment_id=entry["assessment_id"],
            counts=technical["counts"],
        )
    manual = entry.get("manual_review")
    view["manual_review"] = manual["effective_status"] if manual else None
    return view


def build_snapshot(path, config) -> Snapshot:
    probe = connect_readonly(path)
    try:
        mark = signature(probe)
    finally:
        probe.close()
    with Store(path) as store:
        jobs = store.list_jobs(True)
        screened = {r["job_id"]: r for r in processing.inspect_results(store, include_history=True)}
        population = ranking.build(store, config)
        sources: dict[int, list[str]] = {}
        for r in store.db.execute("SELECT job_id,source FROM job_sources ORDER BY rowid"):
            if r[1] not in sources.setdefault(r[0], []):
                sources[r[0]].append(r[1])
        timing = {
            r["job_id"]: r
            for r in store.db.execute(
                "SELECT s.job_id AS job_id, min(s.first_seen) AS first_seen, max(s.last_seen) AS last_seen "
                "FROM job_sources s GROUP BY s.job_id"
            )
        }
    pop_by_id = {r["job_id"]: r for r in population}
    rows = []
    for job in jobs:
        identity = job["id"]
        screening = screened.get(identity, {})
        classification = screening.get("classification")
        relevant = bool(
            (job.get("description") or "").strip() and matching._relevant(job, classification or {})
        )
        entry = pop_by_id.get(identity)
        stamp = timing.get(identity)
        row = {
            "job_id": identity,
            "title": job["title"],
            "company": job["company"],
            "sources": sources.get(identity, []),
            "source_url": job["source_url"],
            "application_url": job["application_url"],
            "workplace": job["workplace_type"] or "unknown",
            "lifecycle": job["status"],
            "listed": job.get("is_listed") == 1,
            "posted_at": job["posted_at"],
            "updated_at": job["updated_at"],
            "first_seen": stamp["first_seen"] if stamp else job["first_seen"],
            "last_seen": stamp["last_seen"] if stamp else job["last_seen"],
            "eligibility": screening.get("status", "UNVERIFIED"),
            "classification_stale": screening.get("stale", True),
            "dimensions": {
                name: part["status"]
                for name, part in (classification or {}).get("merged_decisions", {}).items()
            },
            "relevant": relevant,
            "experience": _experience_text(classification),
            **_assessment_view(entry),
        }
        rows.append(row)
    counts = _counts(rows, population)
    return Snapshot(
        signature=mark,
        built_at=time.time(),
        rows=rows,
        population=population,
        counts=counts,
        by_id={r["job_id"]: r for r in rows},
        population_by_id=pop_by_id,
    )


def _counts(rows, population) -> dict:
    def tally(items):
        out = {"ELIGIBLE": 0, "INELIGIBLE": 0, "UNVERIFIED": 0}
        for r in items:
            out[r["eligibility"]] = out.get(r["eligibility"], 0) + 1
        return out

    active = [r for r in rows if r["lifecycle"] == "active" and r["listed"]]
    statuses = [r["assessment_status"] for r in population if not r["restricted"]]
    return {
        "total": len(rows),
        "active_listed": len(active),
        "relevant_active_listed": sum(r["relevant"] for r in active),
        "eligibility_all": tally(rows),
        "eligibility_active": tally(active),
        "lifecycle": {
            state: sum(r["lifecycle"] == state for r in rows)
            for state in ("active", "unknown", "closed", "expired")
        },
        "stale_classifications": sum(r["classification_stale"] for r in rows),
        "population": len(population),
        "assessments_current": statuses.count("current"),
        "assessments_stale": statuses.count("stale"),
        "awaiting_assessment": statuses.count("missing"),
        "restricted_not_recommended": sum(r["restricted"] for r in population),
    }


class Cache:
    """Holds the latest Snapshot; rebuilds it in the background when stored data changes."""

    def __init__(self, path, config, builder=build_snapshot):
        self.path, self.config, self.builder = Path(path), config, builder
        self._snapshot: Snapshot | None = None
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self.error: str | None = None

    def _build(self):
        for _attempt in range(10):
            try:
                snap = self.builder(self.path, self.config)
            except OSError as exc:  # writer lock held by a collector, CLI command or a write
                self.error = f"Database busy: {exc}"
                time.sleep(0.5)
                continue
            except Exception as exc:  # keep the previous snapshot; show the reason
                self.error = f"{type(exc).__name__}: {exc}"
                return
            with self._lock:
                self._snapshot, self.error = snap, None
            return

    def wait(self, timeout=120) -> None:
        """Block until a background refresh (if any) has finished."""
        if self._worker is not None:
            self._worker.join(timeout)

    def warm(self) -> None:
        self._build()

    def current(self) -> tuple[Snapshot | None, bool]:
        """(snapshot, refreshing). Builds synchronously only when nothing exists yet."""
        if self._snapshot is None:
            self._build()
            return self._snapshot, False
        db = connect_readonly(self.path)
        try:
            changed = signature(db) != self._snapshot.signature
        finally:
            db.close()
        busy = self._worker is not None and self._worker.is_alive()
        if changed and not busy:
            self._worker = threading.Thread(target=self._build, daemon=True)
            self._worker.start()
            busy = True
        return self._snapshot, changed and busy


# --- Job Explorer ---------------------------------------------------------------------

EXPLORER_DEFAULTS = {
    "q": "",
    "relevance": "engineering",
    "source": "",
    "listing": "active_listed",
    "eligibility": "open",
    "workplace": "",
    "assessment": "",
    "tracked": "",
    "sort": "discovered",
    "page": "1",
}


def explorer_params(raw: dict) -> dict:
    """Form values win (an empty value means "all"); absent keys take the engineering default."""
    params = {
        key: (str(raw[key]).strip() if key in raw else default)
        for key, default in EXPLORER_DEFAULTS.items()
    }
    if params["sort"] not in SORTS:
        params["sort"] = "discovered"
    if params["sort"] == "match":
        # Jobs without a current assessment have no score; they are not ranked below others.
        params["assessment"] = "current"
    try:
        params["page"] = str(max(1, int(params["page"])))
    except ValueError:
        params["page"] = "1"
    return params


def _text_key(value):
    return (value or "").casefold()


def explore(snapshot: Snapshot, applications: dict[int, dict], raw: dict) -> dict:
    p = explorer_params(raw)
    needle = p["q"].casefold()
    result = []
    for row in snapshot.rows:
        if p["relevance"] == "engineering" and not row["relevant"]:
            continue
        if p["listing"] == "active_listed" and not (row["lifecycle"] == "active" and row["listed"]):
            continue
        if p["listing"] == "not_active" and row["lifecycle"] == "active" and row["listed"]:
            continue
        if p["source"] and p["source"] not in row["sources"]:
            continue
        status = row["eligibility"]
        if p["eligibility"] == "open" and status == "INELIGIBLE":
            continue
        if (
            p["eligibility"] in {"ELIGIBLE", "UNVERIFIED", "INELIGIBLE"}
            and status != p["eligibility"]
        ):
            continue
        if p["workplace"] and row["workplace"] != p["workplace"]:
            continue
        kind = row["assessment_status"]
        if p["assessment"] == "current" and kind != "current":
            continue
        if p["assessment"] == "stale" and kind != "stale":
            continue
        if p["assessment"] == "missing" and kind not in {"missing", "not_in_population"}:
            continue
        tracked = applications.get(row["job_id"])
        if p["tracked"] == "any" and not tracked:
            continue
        if p["tracked"] == "none" and tracked:
            continue
        if p["tracked"] in tracking.STATUSES and (not tracked or tracked["status"] != p["tracked"]):
            continue
        if (
            needle
            and needle not in _text_key(row["title"])
            and needle not in _text_key(row["company"])
        ):
            continue
        result.append(row)
    sort = p["sort"]
    if sort == "match":
        result.sort(key=lambda r: (-r["score"], -r["coverage"], r["job_id"]))
    elif sort == "company":
        result.sort(key=lambda r: (_text_key(r["company"]), _text_key(r["title"]), r["job_id"]))
    elif sort == "title":
        result.sort(key=lambda r: (_text_key(r["title"]), _text_key(r["company"]), r["job_id"]))
    elif sort == "updated":
        result.sort(
            key=lambda r: (r["updated_at"] or r["last_seen"] or "", r["job_id"]), reverse=True
        )
    else:
        result.sort(key=lambda r: (r["first_seen"] or "", r["job_id"]), reverse=True)
    page = int(p["page"])
    pages = max(1, -(-len(result) // PAGE_SIZE))
    page = min(page, pages)
    start = (page - 1) * PAGE_SIZE
    items = [
        {**r, "application": applications.get(r["job_id"])}
        for r in result[start : start + PAGE_SIZE]
    ]
    return {
        "params": {**p, "page": str(page)},
        "total": len(result),
        "page": page,
        "pages": pages,
        "items": items,
    }


# --- Job detail -------------------------------------------------------------------------


def _load_classification(db, job_id):
    row = db.execute("SELECT * FROM job_classifications WHERE job_id=?", (job_id,)).fetchone()
    if not row:
        return None, None
    return json.loads(row["result"]), {
        "rules_version": row["rules_version"],
        "processed_at": row["processed_at"],
    }


def _assessment_rows(db, job_id):
    return [
        {
            "id": r["id"],
            "model": r["model"],
            "score": r["score"],
            "created_at": r["created_at"],
            "profile_version": r["profile_version"],
        }
        for r in db.execute(
            "SELECT id,model,score,created_at,profile_version FROM ai_assessments "
            "WHERE job_id=? ORDER BY id DESC",
            (job_id,),
        )
    ]


def job_detail(path, config, snapshot: Snapshot, job_id: int) -> dict | None:
    db = connect_readonly(path)
    try:
        job = db.execute("SELECT * FROM job_details WHERE id=?", (job_id,)).fetchone()
        if job is None:
            return None
        sources = []
        for r in db.execute(
            "SELECT source,board,source_id,lifecycle_state,first_seen,last_seen,last_checked_at,"
            "json_extract(payload,'$.source_url') AS source_url,"
            "json_extract(payload,'$.application_url') AS application_url,"
            "json_extract(payload,'$.is_listed') AS is_listed "
            "FROM job_sources WHERE job_id=? ORDER BY source,board,source_id",
            (job_id,),
        ):
            sources.append(dict(r))
        classification, meta = _load_classification(db, job_id)
        row = snapshot.by_id.get(job_id)
        entry = snapshot.population_by_id.get(job_id)
        store = ReadStore(db)
        explanation = None
        if entry is not None:
            explanation = ranking.explain(store, config, job_id, population=snapshot.population)
        return {
            "job": dict(job),
            "row": row,
            "sources": sources,
            "classification": None if (row and row["classification_stale"]) else classification,
            "classification_meta": meta,
            "entry": entry,
            "explanation": explanation,
            "assessments": _assessment_rows(db, job_id),
            "reviews": reviews.history(store, job_id),
            "application": tracking.get(db, job_id),
            "application_history": tracking.history(db, job_id),
            "profile": approved_profile_summary(db),
        }
    finally:
        db.close()


def approved_profile_summary(db) -> dict | None:
    row = db.execute(
        "SELECT version,profile_hash,created_at,profile FROM candidate_profiles "
        "WHERE approved=1 ORDER BY version DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    profile = json.loads(row["profile"])
    months = profile.get("experience_months")
    if months is None and profile.get("experience_years") is not None:
        months = round(float(profile["experience_years"]) * 12)
    return {
        "version": row["version"],
        "hash": row["profile_hash"][:12],
        "approved_months": months,
        "skills": len(profile.get("skills", [])),
        "experience_entries": len(profile.get("experience", [])),
        "projects": len(profile.get("projects", [])),
    }


# --- Review queue -----------------------------------------------------------------------

UNRESOLVED_DIMENSIONS = ("geography", "restrictions", "experience", "workplace", "category")


def review_queue(path, snapshot: Snapshot, raw: dict) -> dict:
    """Relevant, still-open jobs whose Module 2 result has at least one unverified dimension."""
    dimension = (raw.get("dimension") or "").strip()
    state = (raw.get("review") or "").strip()
    items = []
    for entry in snapshot.population:
        if entry["restricted"] or entry["module2"]["status"] != "UNVERIFIED":
            continue
        unresolved = [
            name
            for name in UNRESOLVED_DIMENSIONS
            if entry["module2"]["dimensions"].get(name) == "UNVERIFIED"
        ]
        manual = entry["manual_review"]
        effective = manual["effective_status"] if manual else "none"
        if dimension and dimension not in unresolved:
            continue
        if state == "none" and manual:
            continue
        if state == "needs_reverification" and effective != "needs_reverification":
            continue
        if state == "reviewed" and not manual:
            continue
        items.append(
            {
                "job_id": entry["job_id"],
                "title": entry["title"],
                "company": entry["company"],
                "unresolved": unresolved,
                "stale": entry["module2"]["stale"],
                "review": manual,
                "assessment_status": entry["assessment_status"],
            }
        )
    items.sort(
        key=lambda i: (
            0 if i["review"] and i["review"]["effective_status"] == "needs_reverification" else 1,
            0 if "geography" in i["unresolved"] else 1,
            i["job_id"],
        )
    )
    try:
        page = max(1, int(raw.get("page") or 1))
    except ValueError:
        page = 1
    pages = max(1, -(-len(items) // PAGE_SIZE))
    page = min(page, pages)
    start = (page - 1) * PAGE_SIZE
    chosen = items[start : start + PAGE_SIZE]
    db = connect_readonly(path)
    try:
        for item in chosen:
            classification, _ = _load_classification(db, item["job_id"])
            item["evidence"] = _evidence_for(classification, item["unresolved"])
    finally:
        db.close()
    return {
        "total": len(items),
        "page": page,
        "pages": pages,
        "items": chosen,
        "filters": {"dimension": dimension, "review": state},
    }


def _evidence_for(classification, unresolved) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    if not classification:
        return out
    for name in unresolved:
        quotes = []
        for variant in classification.get("variants", []):
            part = variant.get("decisions", {}).get(name, {})
            for e in part.get("evidence", [])[:2]:
                quotes.append({"rule": e.get("rule"), "quote": e.get("quote")})
            if not part.get("evidence"):
                quotes.append({"rule": None, "quote": None, "reason": part.get("reason")})
        out[name] = quotes[:3]
    return out
