"""Local job-application tracking: one record per job, with append-only status history.

Tracking is the user's own bookkeeping. It is independent of the source job's lifecycle
(a closed job keeps its application), of Module 2 eligibility and of Module 3 scores, and
nothing here contacts an employer. Saving a job never implies that it was applied for.

`job_id` is validated when a record is created but is deliberately not a SQL foreign key,
for the same reason as `eligibility_reviews`: collection can merge duplicate jobs by
deleting a `jobs` row, and that must not be blocked by (or silently delete) the user's
application. `Store.upsert` re-points an application to the surviving job when it merges.
"""

import re
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

SCHEMA_VERSION = 1
STATUSES = (
    "SAVED",
    "PLANNED",
    "APPLIED",
    "SCREENING",
    "INTERVIEW",
    "OFFER",
    "REJECTED",
    "WITHDRAWN",
)
MEANINGS = {
    "SAVED": "Bookmarked for later. This does not mean you have applied.",
    "PLANNED": "You intend to apply (to-apply list).",
    "APPLIED": "You submitted an application yourself.",
    "SCREENING": "An employer is screening or has replied (recruiter contact, test, call).",
    "INTERVIEW": "Interview stage.",
    "OFFER": "An offer was received.",
    "REJECTED": "The employer declined.",
    "WITHDRAWN": "You withdrew or dropped the application.",
}
# Statuses that mean an application was sent, so a date applied must exist.
NEEDS_APPLIED_DATE = frozenset({"APPLIED", "SCREENING", "INTERVIEW", "OFFER"})
IN_PROGRESS = ("APPLIED", "SCREENING", "INTERVIEW")
MAX_NOTES = 5000
MAX_SHORT = 200
MAX_REFERENCE = 500
TABLE, HISTORY = "job_applications", "job_application_history"
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
UNCHANGED = object()


def has_schema(db) -> bool:
    return bool(
        db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone()
        and db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (HISTORY,)
        ).fetchone()
    )


def ensure_schema(store) -> bool:
    """Create the tracking tables. Returns True if anything was created; repeatable.

    An existing database file is backed up (and the backup checked) before the first
    migration. The migration is additive only: no existing table is altered.
    """
    if has_schema(store.db):
        return False
    path = store.db.execute("PRAGMA database_list").fetchone()[2]
    if path:
        folder = Path(path).parent / "backups"
        folder.mkdir(exist_ok=True)
        backup = (
            folder / f"{Path(path).stem}-pre-tracking-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.sqlite3"
        )
        with sqlite3.connect(backup) as target:
            store.db.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Tracking backup failed integrity check")
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        store.db.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE} (
            id INTEGER PRIMARY KEY,
            job_id INTEGER NOT NULL UNIQUE,
            status TEXT NOT NULL CHECK(status IN ({",".join(repr(s) for s in STATUSES)})),
            saved_at TEXT NOT NULL,
            applied_on TEXT CHECK(applied_on IS NULL OR applied_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
            follow_up_on TEXT CHECK(follow_up_on IS NULL OR follow_up_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
            channel TEXT,
            reference TEXT,
            notes TEXT,
            updated_at TEXT NOT NULL,
            CHECK(status NOT IN ('APPLIED','SCREENING','INTERVIEW','OFFER') OR applied_on IS NOT NULL)
        )""")
        store.db.execute(f"CREATE INDEX IF NOT EXISTS job_applications_status ON {TABLE}(status)")
        store.db.execute(
            f"CREATE INDEX IF NOT EXISTS job_applications_follow_up ON {TABLE}(follow_up_on)"
        )
        store.db.execute(f"""CREATE TABLE IF NOT EXISTS {HISTORY} (
            id INTEGER PRIMARY KEY,
            application_id INTEGER NOT NULL REFERENCES {TABLE}(id),
            job_id INTEGER NOT NULL,
            event TEXT NOT NULL CHECK(event IN ('created','status_changed','details_updated')),
            from_status TEXT,
            to_status TEXT NOT NULL,
            summary TEXT,
            changed_at TEXT NOT NULL
        )""")
        store.db.execute(
            f"CREATE INDEX IF NOT EXISTS job_application_history_app ON {HISTORY}(application_id,id)"
        )
        # A reference to a non-existent job is refused by the database itself.
        store.db.execute(
            f"""CREATE TRIGGER IF NOT EXISTS job_applications_job_exists
            BEFORE INSERT ON {TABLE}
            WHEN NOT EXISTS (SELECT 1 FROM jobs WHERE id=NEW.job_id)
            BEGIN SELECT RAISE(ABORT, 'application must reference an existing job'); END"""
        )
        for action in ("UPDATE", "DELETE"):
            store.db.execute(
                f"CREATE TRIGGER IF NOT EXISTS job_application_history_no_{action.lower()} "
                f"BEFORE {action} ON {HISTORY} BEGIN "
                "SELECT RAISE(ABORT, 'application history is append-only'); END"
            )
        store.db.execute(
            "CREATE TABLE IF NOT EXISTS app_tracking_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        store.db.execute(
            "INSERT OR IGNORE INTO app_tracking_meta VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
    return True


# --- validation -----------------------------------------------------------------------


def _day(value, name, *, today, earliest=None, latest=None):
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if not _DATE.fullmatch(text):
        raise ValueError(f"{name} must be a date in YYYY-MM-DD format")
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{name} is not a valid calendar date") from exc
    if earliest and parsed < earliest:
        raise ValueError(f"{name} is too far in the past")
    if latest and parsed > latest:
        raise ValueError(f"{name} is too far in the future")
    return parsed.isoformat()


def _text(value, name, limit):
    if value is None:
        return None
    text = str(value).replace("\r\n", "\n").strip()
    if any(ord(c) < 32 and c not in "\n\t" for c in text):
        raise ValueError(f"{name} contains control characters")
    if len(text) > limit:
        raise ValueError(f"{name} exceeds {limit} characters")
    return text or None


def _reference(value):
    text = _text(value, "reference", MAX_REFERENCE)
    if text is None:
        return None
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", text) and not re.match(r"^https?://", text, re.I):
        # Only web links are accepted as links; other schemes (javascript:, file:) are refused.
        if "://" in text or text.lower().startswith(("javascript:", "data:", "vbscript:")):
            raise ValueError("reference must be a http(s) link or plain text")
    if re.match(r"^https?://", text, re.I):
        parts = urlsplit(text)
        if not parts.netloc or any(c.isspace() for c in text):
            raise ValueError("reference is not a valid http(s) link")
    return text


def safe_link(value) -> str | None:
    """The value if it is an http(s) URL that is safe to render as a link, else None."""
    if not value:
        return None
    text = str(value).strip()
    parts = urlsplit(text)
    if parts.scheme in {"http", "https"} and parts.netloc and not any(c.isspace() for c in text):
        return text
    return None


def _today(now):
    return (now or datetime.now(UTC)).date()


# --- reads (any sqlite3 connection with row_factory=Row) -----------------------------


def _row(row) -> dict | None:
    return dict(row) if row else None


def get(db, job_id) -> dict | None:
    if not has_schema(db):
        return None
    return _row(db.execute(f"SELECT * FROM {TABLE} WHERE job_id=?", (job_id,)).fetchone())


def history(db, job_id) -> list[dict]:
    if not has_schema(db):
        return []
    return [
        dict(r)
        for r in db.execute(
            f"SELECT h.* FROM {HISTORY} h JOIN {TABLE} a ON a.id=h.application_id "
            "WHERE a.job_id=? ORDER BY h.id",
            (job_id,),
        )
    ]


def statuses_by_job(db) -> dict[int, dict]:
    if not has_schema(db):
        return {}
    return {
        r["job_id"]: dict(r)
        for r in db.execute(f"SELECT job_id,status,applied_on,follow_up_on FROM {TABLE}")
    }


def counts(db) -> dict:
    zero = {status: 0 for status in STATUSES}
    if not has_schema(db):
        return {"by_status": zero, "total": 0, "submitted": 0, "in_progress": 0}
    for status, n in db.execute(f"SELECT status,count(*) FROM {TABLE} GROUP BY status"):
        zero[status] = n
    submitted = db.execute(f"SELECT count(*) FROM {TABLE} WHERE applied_on IS NOT NULL").fetchone()[
        0
    ]
    return {
        "by_status": zero,
        "total": sum(zero.values()),
        "submitted": submitted,
        "in_progress": sum(zero[s] for s in IN_PROGRESS),
    }


def list_applications(db, *, status=None, query=None, limit=25, offset=0) -> dict:
    """Applications with their job summary (the job may be closed, unlisted or merged away)."""
    if not has_schema(db):
        return {"total": 0, "items": []}
    where, params = [], []
    if status:
        if status not in STATUSES:
            raise ValueError(f"status must be one of: {', '.join(STATUSES)}")
        where.append("a.status=?")
        params.append(status)
    if query and query.strip():
        like = f"%{query.strip().lower()}%"
        where.append("(lower(d.title) LIKE ? OR lower(d.company) LIKE ?)")
        params += [like, like]
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    base = f"FROM {TABLE} a LEFT JOIN job_details d ON d.id=a.job_id{clause}"
    total = db.execute(f"SELECT count(*) {base}", params).fetchone()[0]
    rows = db.execute(
        "SELECT a.*, d.title, d.company, d.status AS job_status, d.is_listed, d.source_url, "
        "d.application_url "
        f"{base} ORDER BY (a.follow_up_on IS NULL), a.follow_up_on, a.updated_at DESC "
        "LIMIT ? OFFSET ?",
        [*params, max(1, min(limit, 200)), max(0, offset)],
    ).fetchall()
    return {"total": total, "items": [dict(r) for r in rows]}


# --- writes (need a Store: writer lock + transaction) ---------------------------------


def _job_exists(db, job_id) -> bool:
    return bool(db.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone())


def _check_job(db, job_id):
    if not isinstance(job_id, int) or isinstance(job_id, bool) or job_id < 1:
        raise ValueError("job id must be a positive integer")
    if not _job_exists(db, job_id):
        raise ValueError(f"No job with id {job_id}")


def _event(db, app, event, from_status, to_status, summary, stamp):
    db.execute(
        f"INSERT INTO {HISTORY}(application_id,job_id,event,from_status,to_status,summary,"
        "changed_at) VALUES (?,?,?,?,?,?,?)",
        (app, app_job(db, app), event, from_status, to_status, summary, stamp),
    )


def app_job(db, application_id):
    return db.execute(f"SELECT job_id FROM {TABLE} WHERE id=?", (application_id,)).fetchone()[0]


def save_job(store, job_id, now=None) -> dict:
    """Bookmark a job as SAVED. Idempotent: an existing record is returned unchanged."""
    ensure_schema(store)
    _check_job(store.db, job_id)
    stamp = (now or datetime.now(UTC)).isoformat()
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        existing = store.db.execute(f"SELECT * FROM {TABLE} WHERE job_id=?", (job_id,)).fetchone()
        if existing:
            return {**dict(existing), "created": False}
        app = store.db.execute(
            f"INSERT INTO {TABLE}(job_id,status,saved_at,updated_at) VALUES (?,?,?,?)",
            (job_id, "SAVED", stamp, stamp),
        ).lastrowid
        _event(store.db, app, "created", None, "SAVED", "Saved; not applied", stamp)
        created = store.db.execute(f"SELECT * FROM {TABLE} WHERE id=?", (app,)).fetchone()
    return {**dict(created), "created": True}


def set_status(store, job_id, status, *, applied_on=None, note=None, now=None) -> dict:
    """Move a job to `status` (creating its record if the user goes straight to a status).

    Any status may follow any other, because the user may be correcting a mistake; every
    change is recorded. A status that means "sent" needs a date applied: it defaults to
    today only when moving to APPLIED, otherwise it must already exist or be given.
    """
    if status not in STATUSES:
        raise ValueError(f"status must be one of: {', '.join(STATUSES)}")
    ensure_schema(store)
    _check_job(store.db, job_id)
    moment = now or datetime.now(UTC)
    today = moment.date()
    horizon = today + timedelta(days=1)
    day = _day(
        applied_on,
        "date applied",
        today=today,
        earliest=today - timedelta(days=3650),
        latest=horizon,
    )
    comment = _text(note, "note", MAX_SHORT)
    stamp = moment.isoformat()
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        current = store.db.execute(f"SELECT * FROM {TABLE} WHERE job_id=?", (job_id,)).fetchone()
        applied = day or (current["applied_on"] if current else None)
        if status == "APPLIED" and applied is None:
            applied = today.isoformat()
        if status in NEEDS_APPLIED_DATE and applied is None:
            raise ValueError(f"{status} needs the date you applied (YYYY-MM-DD)")
        if current is None:
            app = store.db.execute(
                f"INSERT INTO {TABLE}(job_id,status,saved_at,applied_on,updated_at) "
                "VALUES (?,?,?,?,?)",
                (job_id, status, stamp, applied, stamp),
            ).lastrowid
            _event(store.db, app, "created", None, status, comment, stamp)
        else:
            if current["status"] == status and applied == current["applied_on"]:
                return {**dict(current), "changed": False}
            store.db.execute(
                f"UPDATE {TABLE} SET status=?, applied_on=?, updated_at=? WHERE id=?",
                (status, applied, stamp, current["id"]),
            )
            _event(
                store.db,
                current["id"],
                "status_changed",
                current["status"],
                status,
                comment,
                stamp,
            )
        row = store.db.execute(f"SELECT * FROM {TABLE} WHERE job_id=?", (job_id,)).fetchone()
    return {**dict(row), "changed": True}


def update_details(
    store,
    job_id,
    *,
    follow_up_on=UNCHANGED,
    channel=UNCHANGED,
    reference=UNCHANGED,
    notes=UNCHANGED,
    applied_on=UNCHANGED,
    now=None,
) -> dict:
    """Edit optional details of an existing record. Notes stay local; history stores field names only."""
    ensure_schema(store)
    _check_job(store.db, job_id)
    moment = now or datetime.now(UTC)
    today = moment.date()
    fields = {}
    if follow_up_on is not UNCHANGED:
        fields["follow_up_on"] = _day(
            follow_up_on,
            "follow-up date",
            today=today,
            earliest=today - timedelta(days=365),
            latest=today + timedelta(days=3650),
        )
    if applied_on is not UNCHANGED:
        fields["applied_on"] = _day(
            applied_on,
            "date applied",
            today=today,
            earliest=today - timedelta(days=3650),
            latest=today + timedelta(days=1),
        )
    if channel is not UNCHANGED:
        fields["channel"] = _text(channel, "channel", 100)
    if reference is not UNCHANGED:
        fields["reference"] = _reference(reference)
    if notes is not UNCHANGED:
        fields["notes"] = _text(notes, "notes", MAX_NOTES)
    stamp = moment.isoformat()
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        current = store.db.execute(f"SELECT * FROM {TABLE} WHERE job_id=?", (job_id,)).fetchone()
        if current is None:
            raise ValueError("Save the job or choose a status before adding application details")
        changed = {k: v for k, v in fields.items() if current[k] != v}
        if not changed:
            return {**dict(current), "changed": False}
        if "applied_on" in changed and changed["applied_on"] is None:
            if current["status"] in NEEDS_APPLIED_DATE:
                raise ValueError(f"{current['status']} needs the date you applied")
        sets = ", ".join(f"{name}=?" for name in changed)
        store.db.execute(
            f"UPDATE {TABLE} SET {sets}, updated_at=? WHERE id=?",
            [*changed.values(), stamp, current["id"]],
        )
        _event(
            store.db,
            current["id"],
            "details_updated",
            current["status"],
            current["status"],
            "Updated: " + ", ".join(sorted(changed)),
            stamp,
        )
        row = store.db.execute(f"SELECT * FROM {TABLE} WHERE job_id=?", (job_id,)).fetchone()
    return {**dict(row), "changed": True}


def repoint_after_merge(db, target, other) -> None:
    """Move an application from a merged-away job to the surviving job when possible."""
    if not has_schema(db):
        return
    if not db.execute(f"SELECT 1 FROM {TABLE} WHERE job_id=?", (target,)).fetchone():
        db.execute(f"UPDATE {TABLE} SET job_id=? WHERE job_id=?", (target, other))
    # History rows keep their original job_id (they are append-only and read via application_id).
    # If both jobs already have an application, the older record keeps its original job id
    # and is reported as unattached by orphans(); neither record is deleted or rewritten.


def orphans(db) -> list[int]:
    """Application job ids whose job row no longer exists (should normally be empty)."""
    if not has_schema(db):
        return []
    return [
        r[0]
        for r in db.execute(
            f"SELECT a.job_id FROM {TABLE} a LEFT JOIN jobs j ON j.id=a.job_id WHERE j.id IS NULL"
        )
    ]
