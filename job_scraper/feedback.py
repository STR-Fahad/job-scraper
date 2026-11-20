"""Append-only record of the user's own verdict on a job: "Good fit" or "Not for me".

This is the user's opinion about a job the tool tagged (or did not tag). It never changes a tag,
an assessment, a classification or a tracker status; it exists so the dashboard can keep the
user's picks in their own tabs and so the tool's tags can later be compared with the user's real
answers. Each row stores the tag and assessment the user saw. The newest row per job is the
current pick; `cleared` withdraws a pick. Rows cannot be updated or deleted.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

TABLE = "fit_feedback"
VERDICTS = ("good_fit", "not_for_me", "cleared")
LABELS = {"good_fit": "Good fit", "not_for_me": "Not for me"}
MAX_NOTE = 1000


def has_schema(db) -> bool:
    return bool(
        db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone()
    )


def ensure_schema(store) -> None:
    """Create the table. An existing database is backed up and integrity-checked first."""
    if has_schema(store.db):
        return
    path = store.db.execute("PRAGMA database_list").fetchone()[2]
    if path:
        folder = Path(path).parent / "backups"
        folder.mkdir(exist_ok=True)
        stamp = f"{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}"
        backup = folder / f"{Path(path).stem}-pre-feedback-{stamp}.sqlite3"
        with sqlite3.connect(backup) as target:
            store.db.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Feedback backup failed integrity check")
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        # job_id is deliberately not a foreign key: a pick must survive job deduplication.
        store.db.execute(f"""CREATE TABLE {TABLE} (
            id INTEGER PRIMARY KEY,
            job_id INTEGER NOT NULL,
            verdict TEXT NOT NULL CHECK(verdict IN ('good_fit','not_for_me','cleared')),
            note TEXT,
            final_status TEXT,
            final_code TEXT,
            assessment_id INTEGER,
            created_at TEXT NOT NULL)""")
        store.db.execute(f"CREATE INDEX fit_feedback_job ON {TABLE}(job_id, id)")
        for action in ("UPDATE", "DELETE"):
            store.db.execute(
                f"CREATE TRIGGER fit_feedback_no_{action.lower()} "
                f"BEFORE {action} ON {TABLE} BEGIN "
                "SELECT RAISE(ABORT, 'fit feedback is append-only'); END"
            )


def record(
    store,
    job_id,
    verdict,
    *,
    note=None,
    final_status=None,
    final_code=None,
    assessment_id=None,
    now=None,
) -> dict:
    """Append the user's pick for a job, with the tag they were looking at."""
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of: {', '.join(VERDICTS)}")
    if not store.db.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone():
        raise ValueError(f"No job with id {job_id}")
    text = (note or "").strip() or None
    if text and len(text) > MAX_NOTE:
        raise ValueError(f"note exceeds {MAX_NOTE} characters")
    ensure_schema(store)
    with store.db:
        new_id = store.db.execute(
            f"INSERT INTO {TABLE}(job_id,verdict,note,final_status,final_code,assessment_id,"
            "created_at) VALUES (?,?,?,?,?,?,?)",
            (
                job_id,
                verdict,
                text,
                final_status,
                final_code,
                assessment_id,
                now or datetime.now(UTC).isoformat(),
            ),
        ).lastrowid
    row = store.db.execute(f"SELECT * FROM {TABLE} WHERE id=?", (new_id,)).fetchone()
    return _entry(row)


def _entry(row) -> dict:
    keys = ("id", "job_id", "verdict", "note", "final_status", "final_code", "assessment_id")
    names = (*keys, "created_at")
    return {name: row[index] for index, name in enumerate(names)}


def history(db, job_id=None) -> list[dict]:
    """Every row, oldest first. An absent table means nothing was ever picked."""
    if not has_schema(db):
        return []
    query = (
        "SELECT id,job_id,verdict,note,final_status,final_code,assessment_id,created_at "
        f"FROM {TABLE}" + (" WHERE job_id=?" if job_id is not None else "") + " ORDER BY id"
    )
    return [_entry(r) for r in db.execute(query, (job_id,) if job_id is not None else ())]


def latest(db) -> dict[int, dict]:
    """The current pick per job (a cleared pick is not a pick)."""
    newest = {entry["job_id"]: entry for entry in history(db)}
    return {job_id: e for job_id, e in newest.items() if e["verdict"] != "cleared"}


def report(db) -> dict:
    """The user's picks set against the tool's tag at the moment of each pick (calibration)."""
    picks = list(latest(db).values())
    tags = ("ELIGIBLE", "UNVERIFIED", "INELIGIBLE", "none")
    matrix = {tag: {"good_fit": 0, "not_for_me": 0} for tag in tags}
    by_rule: dict[str, dict] = {}
    for entry in picks:
        tag = entry["final_status"] or "none"
        matrix[tag][entry["verdict"]] += 1
        rule = by_rule.setdefault(
            entry["final_code"] or "none", {"tag": tag, "good_fit": 0, "not_for_me": 0}
        )
        rule[entry["verdict"]] += 1

    def share(part, whole):
        return round(100 * part / whole) if whole else None

    total = {tag: sum(matrix[tag].values()) for tag in tags}
    return {
        "picks": len(picks),
        "matrix": matrix,
        "by_rule": dict(sorted(by_rule.items())),
        # of the jobs the tool called Eligible, how many you confirmed
        "eligible_confirmed_pct": share(matrix["ELIGIBLE"]["good_fit"], total["ELIGIBLE"]),
        # of the jobs the tool called Ineligible, how many you would still apply to (misses)
        "ineligible_wanted_pct": share(matrix["INELIGIBLE"]["good_fit"], total["INELIGIBLE"]),
        "unverified_wanted_pct": share(matrix["UNVERIFIED"]["good_fit"], total["UNVERIFIED"]),
        "agreement_pct": share(
            matrix["ELIGIBLE"]["good_fit"] + matrix["INELIGIBLE"]["not_for_me"],
            total["ELIGIBLE"] + total["INELIGIBLE"],
        ),
    }


def render_report(data: dict) -> str:
    """Plain-text calibration summary for the CLI."""
    if not data["picks"]:
        return "No Good fit / Not for me picks yet."

    def pct(value):
        return "n/a" if value is None else f"{value}%"

    lines = [
        f"{data['picks']} pick(s). Tool tag when you chose  ->  your answer",
        "  tag          Good fit   Not for me",
    ]
    for tag, counts in data["matrix"].items():
        lines.append(f"  {tag:<12} {counts['good_fit']:>8}   {counts['not_for_me']:>10}")
    lines += [
        f"Eligible tags you confirmed: {pct(data['eligible_confirmed_pct'])}",
        f"Ineligible tags you would still apply to (misses): {pct(data['ineligible_wanted_pct'])}",
        f"Unverified jobs you wanted: {pct(data['unverified_wanted_pct'])}",
        f"Agreement on Eligible/Ineligible tags: {pct(data['agreement_pct'])}",
        "By rule (tool tag, rule, Good fit, Not for me):",
    ]
    lines += [
        f"  {rule}: {v['tag']} | {v['good_fit']} | {v['not_for_me']}"
        for rule, v in data["by_rule"].items()
    ]
    return "\n".join(lines)
