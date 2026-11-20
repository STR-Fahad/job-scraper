"""Append-only blocker-override proposals and decisions (Module 3), kept apart from assessments.

A hard blocker (a Python-confirmed experience gap, or a session judgment's quoted unmet hiring
condition) normally decides INELIGIBLE and nothing can overrule it. In the rare case where the
blocker is a misreading of the posting, the verdict writer or the user may PROPOSE an override: it
names the blocker, quotes the posting line verbatim and says why the blocker is wrong. A proposal
changes nothing. Only the user's explicit approval lifts that one blocker for that one job; the tag
is then derived again from the remaining rules (it is never set to ELIGIBLE directly).

Rows are append-only (database triggers refuse UPDATE and DELETE). The newest row per job and
blocker is the current state. Each row records the id of the assessment the blocker came from; if
the job is judged again the approval is **outdated** (kept, but no longer applied), so a new
assessment is never silently overruled by an old approval.
"""

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

TABLE = "blocker_overrides"
MAX_TEXT = 2000
# blocker name -> the final-decision reason code it can lift
BLOCKERS = {
    "experience_gap": "experience_gap",
    "judgment_blocker": "session_judgment_ineligible",
}
STATUSES = ("proposed", "approved", "rejected")
PROPOSERS = ("judge", "user")


def has_schema(store) -> bool:
    return bool(store.db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)).fetchone())


def ensure_schema(store) -> None:
    """Create the table. An existing database is backed up and integrity-checked first."""
    if has_schema(store):
        return
    path = store.db.execute("PRAGMA database_list").fetchone()[2]
    if path:
        folder = Path(path).parent / "backups"
        folder.mkdir(exist_ok=True)
        stamp = f"{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}"
        backup = folder / f"{Path(path).stem}-pre-overrides-{stamp}.sqlite3"
        with sqlite3.connect(backup) as target:
            store.db.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Override backup failed integrity check")
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        # job_id is deliberately not a foreign key: an override must survive job deduplication.
        store.db.execute(f"""CREATE TABLE {TABLE} (
            id INTEGER PRIMARY KEY,
            job_id INTEGER NOT NULL,
            blocker TEXT NOT NULL CHECK(blocker IN ('experience_gap','judgment_blocker')),
            status TEXT NOT NULL CHECK(status IN ('proposed','approved','rejected')),
            quote TEXT NOT NULL CHECK(length(trim(quote)) > 0),
            reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
            proposed_by TEXT NOT NULL CHECK(proposed_by IN ('judge','user')),
            assessment_id INTEGER NOT NULL,
            parent_id INTEGER,
            note TEXT,
            created_at TEXT NOT NULL)""")
        store.db.execute(f"CREATE INDEX blocker_overrides_job ON {TABLE}(job_id, blocker, id)")
        for action in ("UPDATE", "DELETE"):
            store.db.execute(
                f"CREATE TRIGGER blocker_overrides_no_{action.lower()} "
                f"BEFORE {action} ON {TABLE} BEGIN "
                "SELECT RAISE(ABORT, 'blocker overrides are append-only'); END"
            )


def _clean(value, name) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    if len(text) > MAX_TEXT:
        raise ValueError(f"{name} exceeds {MAX_TEXT} characters")
    return text


def _squash(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _job_text(store, job_id: int) -> str:
    import json

    row = store.db.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise ValueError(f"No job with id {job_id}")
    payload = json.loads(row[0])
    parts = [payload.get("description", ""), *payload.get("location", [])]
    parts += [str(v) for v in payload.get("restrictions", {}).values()]
    return _squash(" ".join(str(p) for p in parts))


def _entry(row) -> dict:
    return {key: row[key] for key in row.keys()}


def propose(store, job_id, blocker, quote, reason, proposed_by, assessment_id, now=None) -> dict:
    """Record a proposal. The quote must appear verbatim in the job's own text."""
    if blocker not in BLOCKERS:
        raise ValueError(f"blocker must be one of: {', '.join(BLOCKERS)}")
    if proposed_by not in PROPOSERS:
        raise ValueError(f"proposed_by must be one of: {', '.join(PROPOSERS)}")
    if not isinstance(assessment_id, int):
        raise ValueError("a current assessment is required to propose an override")
    text = _clean(quote, "quote")
    why = _clean(reason, "reason")
    if _squash(text) not in _job_text(store, job_id):
        raise ValueError("quote is not verbatim in the job text")
    ensure_schema(store)
    with store.db:
        new_id = store.db.execute(
            f"INSERT INTO {TABLE}(job_id,blocker,status,quote,reason,proposed_by,assessment_id,"
            "created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                job_id,
                blocker,
                "proposed",
                text,
                why,
                proposed_by,
                assessment_id,
                now or datetime.now(UTC).isoformat(),
            ),
        ).lastrowid
    return _entry(store.db.execute(f"SELECT * FROM {TABLE} WHERE id=?", (new_id,)).fetchone())


def decide(store, override_id, status, note=None, now=None) -> dict:
    """Approve or reject the newest proposal for a job and blocker (the user's decision)."""
    if status not in {"approved", "rejected"}:
        raise ValueError("status must be approved or rejected")
    if not has_schema(store):
        raise ValueError(f"No override with id {override_id}")
    row = store.db.execute(f"SELECT * FROM {TABLE} WHERE id=?", (override_id,)).fetchone()
    if not row:
        raise ValueError(f"No override with id {override_id}")
    newest = store.db.execute(
        f"SELECT id, status FROM {TABLE} WHERE job_id=? AND blocker=? ORDER BY id DESC LIMIT 1",
        (row["job_id"], row["blocker"]),
    ).fetchone()
    if newest["id"] != row["id"] or row["status"] != "proposed":
        raise ValueError("only the newest, undecided proposal can be approved or rejected")
    with store.db:
        new_id = store.db.execute(
            f"INSERT INTO {TABLE}(job_id,blocker,status,quote,reason,proposed_by,assessment_id,"
            "parent_id,note,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                row["job_id"],
                row["blocker"],
                status,
                row["quote"],
                row["reason"],
                row["proposed_by"],
                row["assessment_id"],
                row["id"],
                (note or "").strip() or None,
                now or datetime.now(UTC).isoformat(),
            ),
        ).lastrowid
    return _entry(store.db.execute(f"SELECT * FROM {TABLE} WHERE id=?", (new_id,)).fetchone())


def history(store, job_id=None) -> list[dict]:
    """Every row, oldest first. An absent table means nothing was ever proposed."""
    if not has_schema(store):
        return []
    query = f"SELECT * FROM {TABLE}" + (" WHERE job_id=?" if job_id is not None else "")
    params = (job_id,) if job_id is not None else ()
    return [_entry(r) for r in store.db.execute(query + " ORDER BY id", params)]


def latest(store) -> dict[int, list[dict]]:
    """The newest row per blocker, grouped by job."""
    newest: dict[tuple[int, str], dict] = {}
    for entry in history(store):
        newest[(entry["job_id"], entry["blocker"])] = entry
    grouped: dict[int, list[dict]] = {}
    for (job_id, _), entry in sorted(newest.items()):
        grouped.setdefault(job_id, []).append(entry)
    return grouped


def annotate(entries, assessment_id) -> list[dict]:
    """Derive the effective state against the job's CURRENT assessment (nothing is rewritten)."""
    result = []
    for entry in entries or []:
        outdated = entry["assessment_id"] != assessment_id
        if outdated:
            effective = "outdated"
        else:
            effective = entry["status"]
        result.append({**entry, "effective": effective})
    return result


def approved_blockers(row: dict) -> dict[str, dict]:
    """blocker -> entry for every approved, non-outdated override on a ranking row."""
    return {e["blocker"]: e for e in row.get("overrides", []) if e["effective"] == "approved"}


def render(entries) -> str:
    """Plain-text listing used by the CLI."""
    if not entries:
        return "No blocker overrides recorded."
    return "\n".join(
        f"#{e['id']} job {e['job_id']} {e['blocker']} {e.get('effective', e['status'])} "
        f"(by {e['proposed_by']}, assessment #{e['assessment_id']}): {e['reason']} "
        f'| quote: "{e["quote"][:120]}"' + (f" | note: {e['note']}" if e.get("note") else "")
        for e in entries
    )
