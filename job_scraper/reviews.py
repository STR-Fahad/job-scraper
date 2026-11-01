"""Append-only manual eligibility observations, kept apart from Module 2 and AI assessments.

An entry records what the user concluded and why. It never changes a classification,
a classification history row or an assessment, and nothing here promotes a job to
ELIGIBLE: Module 2's automated status is always shown separately.

Each entry stores the fingerprint of the job's source records (content and lifecycle) as
they were when the review was made. A review whose fingerprint no longer matches the job
is **outdated**: it is kept, but its eligibility claim is not carried onto the changed
job, and its effective status becomes `needs_reverification`. This is derived at read
time, so the stored row is never rewritten, and the review is current again if the job
returns to the same content or the user records a new review. No evidence is fetched or
checked: an evidence reference is the user's claim, not something the application verified.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .processing import inspect_results, snapshots

MAX_TEXT = 2000
STATUSES = {
    "unresolved": (
        "Reviewed, but Pakistan employment eligibility is still not established "
        "(needs information). No evidence is required."
    ),
    "evidence_supported": (
        "The reviewer cites source evidence that this employer can engage a "
        "Pakistan-based worker. Evidence is required; it is an observation, not a "
        "Module 2 result."
    ),
    "confirmed_restricted": (
        "The reviewer cites source evidence that a Pakistan-based worker cannot be "
        "engaged for this role. Evidence is required."
    ),
}
ASSERTIONS = frozenset({"evidence_supported", "confirmed_restricted"})
TABLE = "eligibility_reviews"
VERIFICATION_NOTE = "User-supplied evidence reference; the application did not verify it."


def has_schema(store) -> bool:
    return bool(store.db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)).fetchone())


def ensure_schema(store) -> None:
    """Create the review table. An existing database is backed up first."""
    if has_schema(store):
        return
    path = store.db.execute("PRAGMA database_list").fetchone()[2]
    if path:
        folder = Path(path).parent / "backups"
        folder.mkdir(exist_ok=True)
        backup = (
            folder / f"{Path(path).stem}-pre-reviews-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.sqlite3"
        )
        with sqlite3.connect(backup) as target:
            store.db.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Review backup failed integrity check")
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        # job_id is deliberately not a foreign key: a review must survive job deduplication.
        store.db.execute(f"""CREATE TABLE {TABLE} (
            id INTEGER PRIMARY KEY,
            job_id INTEGER NOT NULL,
            status TEXT NOT NULL
                CHECK(status IN ('unresolved','evidence_supported','confirmed_restricted')),
            explanation TEXT NOT NULL CHECK(length(trim(explanation)) > 0),
            evidence TEXT CHECK(evidence IS NULL OR length(trim(evidence)) > 0),
            module2_status TEXT NOT NULL,
            classification_fingerprint TEXT,
            reviewed_at TEXT NOT NULL,
            CHECK(status = 'unresolved' OR evidence IS NOT NULL))""")
        store.db.execute(f"CREATE INDEX eligibility_reviews_job ON {TABLE}(job_id, id)")
        for action in ("UPDATE", "DELETE"):
            store.db.execute(
                f"CREATE TRIGGER eligibility_reviews_no_{action.lower()} "
                f"BEFORE {action} ON {TABLE} BEGIN "
                "SELECT RAISE(ABORT, 'eligibility reviews are append-only'); END"
            )


def _clean(value, name, *, required):
    text = (value or "").strip()
    if not text:
        if required:
            raise ValueError(f"{name} is required")
        return None
    if len(text) > MAX_TEXT:
        raise ValueError(f"{name} exceeds {MAX_TEXT} characters")
    return text


def _entry(row) -> dict:
    return {
        "id": row["id"],
        "job_id": row["job_id"],
        "status": row["status"],
        "meaning": STATUSES[row["status"]],
        "explanation": row["explanation"],
        "evidence": row["evidence"],
        "module2_status_at_review": row["module2_status"],
        "classification_fingerprint": row["classification_fingerprint"],
        "reviewed_at": row["reviewed_at"],
        "source": "manual_review",
        "verification": VERIFICATION_NOTE if row["status"] in ASSERTIONS else "none",
    }


def fingerprints(store, job_id=None) -> dict[int, str]:
    """Current fingerprint of each job's source records, as Module 2 computes it."""
    return {identity: value for identity, value, _ in snapshots(store, job_id)}


def _currency(entry, current) -> tuple[str, str | None]:
    recorded = entry["classification_fingerprint"]
    if current is None:
        return "needs_reverification", "job_no_longer_present"
    if not recorded:
        return "needs_reverification", "no_fingerprint_recorded"
    if recorded != current:
        return "needs_reverification", "job_content_changed"
    return "current", None


def annotate(entry, current, *, superseded=False) -> dict:
    """Derive currency and effective status without touching the stored entry."""
    currency, reason = _currency(entry, current)
    effective = entry["status"]
    if currency != "current" and entry["status"] in ASSERTIONS:
        effective = "needs_reverification"
    elif currency != "current":
        effective = "unresolved"
    return {
        **entry,
        "currency": currency,
        "currency_reason": reason,
        "effective_status": effective,
        "superseded": superseded,
    }


def label(entry) -> str:
    """One stable display string used by every command."""
    if not entry:
        return "none"
    state = (
        "current"
        if entry["currency"] == "current"
        else (f"needs_reverification: {entry['currency_reason']}")
    )
    extra = ", superseded" if entry.get("superseded") else ""
    return f"{entry['status']} (#{entry['id']}, {state}{extra})"


def add_review(store, job_id, status, explanation, evidence=None, now=None) -> dict:
    """Append one review. Earlier entries stay; the newest entry is the current view."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of: {', '.join(STATUSES)}")
    text = _clean(explanation, "explanation", required=True)
    cited = _clean(evidence, "evidence", required=status != "unresolved")
    if not store.db.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone():
        raise ValueError(f"No job with id {job_id}")
    screened = inspect_results(store, job_id=job_id, include_history=True)
    module2 = screened[0]["status"] if screened else "UNVERIFIED"
    # The live fingerprint (not the stored classification row, which may itself be stale)
    # describes the job text the reviewer actually saw.
    current = fingerprints(store, job_id).get(job_id)
    ensure_schema(store)
    stamp = now or datetime.now(UTC).isoformat()
    with store.db:
        new_id = store.db.execute(
            f"INSERT INTO {TABLE}(job_id,status,explanation,evidence,module2_status,"
            "classification_fingerprint,reviewed_at) VALUES (?,?,?,?,?,?,?)",
            (job_id, status, text, cited, module2, current, stamp),
        ).lastrowid
    saved = _entry(store.db.execute(f"SELECT * FROM {TABLE} WHERE id=?", (new_id,)).fetchone())
    return annotate(saved, current)


def history(store, job_id=None, current=None) -> list[dict]:
    """Every entry, oldest first, each marked current or needs_reverification.

    `superseded` is true for an entry that a newer entry for the same job replaced.
    An absent table means no review was ever recorded.
    """
    if not has_schema(store):
        return []
    query = f"SELECT * FROM {TABLE}" + (" WHERE job_id=?" if job_id is not None else "")
    params = (job_id,) if job_id is not None else ()
    entries = [_entry(r) for r in store.db.execute(query + " ORDER BY id", params)]
    if current is None:
        current = fingerprints(store, job_id)
    newest = {entry["job_id"]: entry["id"] for entry in entries}
    return [
        annotate(e, current.get(e["job_id"]), superseded=newest[e["job_id"]] != e["id"])
        for e in entries
    ]


def latest(store, current=None) -> dict[int, dict]:
    """The newest entry per job. Older entries are kept and remain in history()."""
    return {entry["job_id"]: entry for entry in history(store, current=current)}
