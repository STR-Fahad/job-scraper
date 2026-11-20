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
# Why the user answered the way they did (optional; several can be chosen).
REASONS = {
    "too_senior": "Too senior or too many years",
    "location": "Location, region or timezone",
    "language": "Language",
    "stack": "Not my stack or skills",
    "not_software": "Not a software role",
    "onsite": "On-site or hybrid",
    "company_pay": "Company or pay",
    "other": "Other",
}


def has_schema(db) -> bool:
    return bool(
        db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone()
    )


def _columns(db) -> set[str]:
    return {row[1] for row in db.execute(f"PRAGMA table_info({TABLE})")}


def _backup(store, label: str) -> None:
    path = store.db.execute("PRAGMA database_list").fetchone()[2]
    if not path:
        return
    folder = Path(path).parent / "backups"
    folder.mkdir(exist_ok=True)
    stamp = f"{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}"
    backup = folder / f"{Path(path).stem}-{label}-{stamp}.sqlite3"
    with sqlite3.connect(backup) as target:
        store.db.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Feedback backup failed integrity check")


def ensure_schema(store) -> None:
    """Create the table (or add the reasons column to an earlier one) after a verified backup."""
    if has_schema(store.db):
        if "reasons" not in _columns(store.db):
            _backup(store, "pre-feedback-reasons")
            with store.db:
                store.db.execute(f"ALTER TABLE {TABLE} ADD COLUMN reasons TEXT")
        return
    _backup(store, "pre-feedback")
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        # job_id is deliberately not a foreign key: a pick must survive job deduplication.
        store.db.execute(f"""CREATE TABLE {TABLE} (
            id INTEGER PRIMARY KEY,
            job_id INTEGER NOT NULL,
            verdict TEXT NOT NULL CHECK(verdict IN ('good_fit','not_for_me','cleared')),
            note TEXT,
            reasons TEXT,
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
    reasons=None,
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
    chosen = {r.strip() for r in (reasons or []) if r and r.strip()}
    unknown = chosen - set(REASONS)
    if unknown:
        raise ValueError(f"unknown reason: {', '.join(sorted(unknown))}")
    reason_text = ",".join(r for r in REASONS if r in chosen) or None
    ensure_schema(store)
    with store.db:
        new_id = store.db.execute(
            f"INSERT INTO {TABLE}(job_id,verdict,note,reasons,final_status,final_code,"
            "assessment_id,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                job_id,
                verdict,
                text,
                reason_text,
                final_status,
                final_code,
                assessment_id,
                now or datetime.now(UTC).isoformat(),
            ),
        ).lastrowid
    row = store.db.execute(f"SELECT * FROM {TABLE} WHERE id=?", (new_id,)).fetchone()
    return _entry(row)


NAMES = (
    "id",
    "job_id",
    "verdict",
    "note",
    "reasons",
    "final_status",
    "final_code",
    "assessment_id",
    "created_at",
)


def _entry(row) -> dict:
    entry = {name: row[index] for index, name in enumerate(NAMES)}
    entry["reason_list"] = [r for r in (entry["reasons"] or "").split(",") if r in REASONS]
    return entry


def history(db, job_id=None) -> list[dict]:
    """Every row, oldest first. An absent table means nothing was ever picked."""
    if not has_schema(db):
        return []
    columns = ",".join(n if n in _columns(db) else f"NULL AS {n}" for n in NAMES)
    query = (
        f"SELECT {columns} FROM {TABLE}"
        + (" WHERE job_id=?" if job_id is not None else "")
        + " ORDER BY id"
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
    by_reason: dict[str, dict] = {}
    for entry in picks:
        if entry["verdict"] != "not_for_me":
            continue
        for reason in entry["reason_list"]:
            slot = by_reason.setdefault(reason, {"total": 0, "by_tag": {}})
            slot["total"] += 1
            tag = entry["final_status"] or "none"
            slot["by_tag"][tag] = slot["by_tag"].get(tag, 0) + 1
    return {
        "by_reason": dict(sorted(by_reason.items(), key=lambda item: (-item[1]["total"], item[0]))),
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
    if data.get("by_reason"):
        lines.append("Why you said Not for me (reason: answers, by tool tag):")
        lines += [
            f"  {REASONS[reason]}: {v['total']} ("
            + ", ".join(f"{tag} {n}" for tag, n in sorted(v["by_tag"].items()))
            + ")"
            for reason, v in data["by_reason"].items()
        ]
    return "\n".join(lines)
