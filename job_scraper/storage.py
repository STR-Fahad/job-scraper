"""Transactional SQLite persistence with separate job, source, and URL identities."""

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .locking import WriterLock
from .migrations import migrate_v2
from .models import Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_sources (
    source TEXT NOT NULL,
    board TEXT NOT NULL,
    source_id TEXT NOT NULL,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (source, board, source_id)
);
CREATE INDEX IF NOT EXISTS sources_job ON job_sources(job_id);
CREATE TABLE IF NOT EXISTS job_urls (
    canonical_url TEXT PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS source_runs (
    source TEXT NOT NULL,
    board TEXT NOT NULL,
    attempted_at REAL NOT NULL,
    PRIMARY KEY (source, board)
);
CREATE TABLE IF NOT EXISTS collection_runs (
    id INTEGER PRIMARY KEY,
    finished_at TEXT NOT NULL,
    statistics TEXT NOT NULL CHECK(json_valid(statistics))
);
CREATE VIEW IF NOT EXISTS job_details AS
SELECT id, first_seen, last_seen,
    json_extract(payload, '$.company') AS company,
    json_extract(payload, '$.title') AS title,
    json_extract(payload, '$.description') AS description,
    json_extract(payload, '$.location') AS location,
    json_extract(payload, '$.restrictions') AS restrictions,
    json_extract(payload, '$.employment_type') AS employment_type,
    json_extract(payload, '$.salary') AS salary,
    json_extract(payload, '$.posted_at') AS posted_at,
    json_extract(payload, '$.updated_at') AS updated_at,
    json_extract(payload, '$.source_url') AS source_url,
    json_extract(payload, '$.application_url') AS application_url
FROM jobs;
"""


@dataclass(frozen=True)
class UpsertResult:
    action: str
    duplicate: bool = False
    merged: int = 0
    expired: bool = False


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = WriterLock(path)
        self.lock.__enter__()
        try:
            self._open(path)
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            self.lock.__exit__()
            raise

    def _open(self, path):
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2):
            self.db.close()
            raise ValueError(f"Unsupported database schema version {version}")
        if version == 0:
            self.db.executescript(SCHEMA)
        if version < 2:
            migrate_v2(self.db, path)
            with self.db:
                for row in self.db.execute("SELECT id FROM jobs").fetchall():
                    self._refresh_job(row[0])
        self.expire_jobs()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.close()
        self.lock.__exit__(*args)

    def claim_run(self, source: str, board: str, cooldown: float, now=None) -> bool:
        """Persist attempt reservation so repeated CLI runs respect feed cooldowns."""
        now = time.time() if now is None else now
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT attempted_at FROM source_runs WHERE source=? AND board=?", (source, board)
            ).fetchone()
            if row and now - row[0] < cooldown:
                return False
            self.db.execute(
                "INSERT INTO source_runs VALUES (?, ?, ?) ON CONFLICT(source, board) "
                "DO UPDATE SET attempted_at=excluded.attempted_at",
                (source, board, now),
            )
        return True

    def upsert(self, job: Job, scan_id=None, checked_at=None) -> UpsertResult:
        now = checked_at or datetime.now(UTC).isoformat()
        payload = job.model_dump_json()
        key = (job.source, job.board, job.source_id)
        urls = sorted(job.identity_urls)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            old = self.db.execute(
                "SELECT * FROM job_sources WHERE source=? AND board=? AND source_id=?", key
            ).fetchone()
            candidates = {old["job_id"]} if old else set()
            for url in urls:
                row = self.db.execute(
                    "SELECT job_id FROM job_urls WHERE canonical_url=?", (url,)
                ).fetchone()
                if row:
                    candidates.add(row[0])
            duplicate = old is None and bool(candidates)
            action = "updated" if old else "inserted"
            if old:
                # Page-level metadata can change independently of a job; retain it
                # but don't report the job as changed solely because of a cursor.
                before = json.loads(old["payload"])
                after = json.loads(payload)
                before.pop("response_metadata", None)
                after.pop("response_metadata", None)
                if before == after:
                    action = "unchanged"
            if candidates:
                target = min(candidates)
                if duplicate:
                    action = "updated"
                for other in candidates - {target}:
                    self.db.execute(
                        "UPDATE jobs SET first_seen=min(first_seen, "
                        "(SELECT first_seen FROM jobs WHERE id=?)) WHERE id=?",
                        (other, target),
                    )
                    self.db.execute(
                        "UPDATE job_sources SET job_id=? WHERE job_id=?", (target, other)
                    )
                    self.db.execute("UPDATE job_urls SET job_id=? WHERE job_id=?", (target, other))
                    self.db.execute("DELETE FROM jobs WHERE id=?", (other,))
            else:
                target = self.db.execute(
                    "INSERT INTO jobs(payload, first_seen, last_seen) VALUES (?,?,?)",
                    (payload, now, now),
                ).lastrowid
            self.db.execute(
                """INSERT INTO job_sources(source,board,source_id,job_id,payload,first_seen,last_seen) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, board, source_id) DO UPDATE SET
                job_id=excluded.job_id, payload=excluded.payload, last_seen=excluded.last_seen""",
                (*key, target, payload, now, now),
            )
            for url in urls:
                self.db.execute(
                    "INSERT INTO job_urls VALUES (?, ?) ON CONFLICT(canonical_url) "
                    "DO UPDATE SET job_id=excluded.job_id",
                    (url, target),
                )
            state = (
                "expired"
                if job.expires_at and job.expires_at <= datetime.fromisoformat(now)
                else "active"
            )
            evidence = (
                {"reason": "explicit_expiry", "expires_at": str(job.expires_at)}
                if state == "expired"
                else {}
            )
            self.db.execute(
                "UPDATE job_sources SET lifecycle_state=?, last_checked_at=?, closure_evidence=?, seen_scan=? WHERE source=? AND board=? AND source_id=?",
                (state, now, json.dumps(evidence), scan_id, *key),
            )
            self._event(
                key,
                old["lifecycle_state"] if old else None,
                state,
                now,
                evidence or {"reason": "observed_in_feed"},
                scan_id,
            )
            self._refresh_job(target)
        return UpsertResult(
            action,
            duplicate,
            max(0, len(candidates) - 1),
            state == "expired" and (old is None or old["lifecycle_state"] != "expired"),
        )

    def _refresh_job(self, job_id):
        rows = self.db.execute(
            "SELECT payload,lifecycle_state,last_seen FROM job_sources WHERE job_id=? ORDER BY source,board,source_id",
            (job_id,),
        ).fetchall()
        if not rows:
            return

        def rank(row):
            p = json.loads(row[0])
            visible = p.get("is_listed", True)
            return (
                row[1] == "active" and visible,
                row[1] == "active",
                visible,
                bool(p.get("description")),
                bool(p.get("salary")),
                len(p.get("description", "")),
            )

        representative = max(rows, key=rank)
        self.db.execute(
            "UPDATE jobs SET payload=?,last_seen=? WHERE id=?",
            (representative[0], max(r[2] for r in rows), job_id),
        )

    def _event(self, key, old, new, checked, evidence, scan_id=None):
        if old != new:
            self.db.execute(
                "INSERT INTO lifecycle_events(source,board,source_id,from_state,to_state,checked_at,evidence,scan_id) VALUES (?,?,?,?,?,?,?,?)",
                (*key, old, new, checked, json.dumps(evidence), scan_id),
            )

    def begin_scan(self, source, board, now=None):
        with self.db:
            return self.db.execute(
                "INSERT INTO scans(source,board,started_at) VALUES (?,?,?)",
                (source, board, time.time() if now is None else now),
            ).lastrowid

    def finish_scan(self, scan_id, stats, *, now=None, empty_confirmation_seconds=3600):
        stamp = time.time() if now is None else now
        checked = datetime.fromtimestamp(stamp, UTC).isoformat()
        with self.db:
            scan = self.db.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
            complete = not (stats.errors or stats.invalid or stats.truncated or stats.skipped)
            stats.exhaustive = complete
            can_close = complete
            if complete and stats.fetched == 0:
                previous = self.db.execute(
                    "SELECT * FROM scans WHERE source=? AND board=? AND id<? ORDER BY id DESC LIMIT 1",
                    (scan["source"], scan["board"], scan_id),
                ).fetchone()
                can_close = bool(
                    previous
                    and previous["exhaustive"]
                    and previous["fetched"] == 0
                    and stamp - previous["started_at"] >= empty_confirmation_seconds
                )
                stats.empty_confirmation_pending = not can_close
            if can_close:
                evidence = json.dumps(
                    {
                        "reason": "absent_from_exhaustive_scan",
                        "scan_id": scan_id,
                        "checked_at": checked,
                        "empty_feed_confirmed": stats.fetched == 0,
                    }
                )
                for row in self.db.execute(
                    "SELECT source,board,source_id,lifecycle_state FROM job_sources WHERE source=? AND board=? AND (seen_scan IS NULL OR seen_scan!=?) AND lifecycle_state IN ('active','unknown')",
                    (scan["source"], scan["board"], scan_id),
                ).fetchall():
                    self._event(
                        tuple(row[:3]), row[3], "closed", checked, json.loads(evidence), scan_id
                    )
                cursor = self.db.execute(
                    "UPDATE job_sources SET lifecycle_state='closed',last_checked_at=?,closure_evidence=? WHERE source=? AND board=? AND (seen_scan IS NULL OR seen_scan!=?) AND lifecycle_state IN ('active','unknown')",
                    (checked, evidence, scan["source"], scan["board"], scan_id),
                )
                stats.closed = cursor.rowcount
            if complete:
                self.db.execute(
                    "UPDATE job_sources SET last_checked_at=? WHERE source=? AND board=?",
                    (checked, scan["source"], scan["board"]),
                )
            self.db.execute(
                "UPDATE scans SET finished_at=?,result=?,exhaustive=?,fetched=? WHERE id=?",
                (
                    stamp,
                    "complete" if complete else "partial",
                    int(complete),
                    stats.fetched,
                    scan_id,
                ),
            )
            for row in self.db.execute(
                "SELECT DISTINCT job_id FROM job_sources WHERE source=? AND board=?",
                (scan["source"], scan["board"]),
            ).fetchall():
                self._refresh_job(row[0])

    def expire_jobs(self, now=None):
        checked = (now or datetime.now(UTC)).isoformat()
        with self.db:
            for row in self.db.execute(
                "SELECT source,board,source_id,lifecycle_state FROM job_sources WHERE lifecycle_state IN ('active','unknown') AND julianday(json_extract(payload,'$.expires_at')) <= julianday(?)",
                (checked,),
            ).fetchall():
                self._event(
                    tuple(row[:3]), row[3], "expired", checked, {"reason": "explicit_expiry"}
                )
            affected = self.db.execute(
                "SELECT DISTINCT job_id FROM job_sources WHERE lifecycle_state IN ('active','unknown') AND julianday(json_extract(payload,'$.expires_at')) <= julianday(?)",
                (checked,),
            ).fetchall()
            cursor = self.db.execute(
                "UPDATE job_sources SET lifecycle_state='expired',closure_evidence=? WHERE lifecycle_state IN ('active','unknown') AND julianday(json_extract(payload,'$.expires_at')) <= julianday(?)",
                (json.dumps({"reason": "explicit_expiry", "evaluated_at": checked}), checked),
            )
            for row in affected:
                self._refresh_job(row[0])
            return cursor.rowcount

    def list_jobs(self, include_history=False):
        self.expire_jobs()
        table = "job_details" if include_history else "discovery_jobs"
        return [dict(r) for r in self.db.execute(f"SELECT * FROM {table} ORDER BY id")]

    def host_deadline(self, host):
        row = self.db.execute(
            "SELECT not_before FROM host_cooldowns WHERE host=?", (host,)
        ).fetchone()
        return row[0] if row else 0

    def defer_host(self, host, deadline):
        with self.db:
            self.db.execute(
                "INSERT INTO host_cooldowns VALUES (?,?) ON CONFLICT(host) DO UPDATE SET not_before=max(not_before,excluded.not_before)",
                (host, deadline),
            )

    def save_statistics(self, statistics):
        with self.db:
            self.db.execute(
                "INSERT INTO collection_runs(finished_at, statistics) VALUES (?, ?)",
                (datetime.now(UTC).isoformat(), statistics.model_dump_json()),
            )
