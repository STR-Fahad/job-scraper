"""Versioned, transactional migrations. Existing files are backed up before DDL."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def migrate_v2(db, path):
    if str(path) != ":memory:":
        path = Path(path)
        folder = path.parent / "backups"
        folder.mkdir(exist_ok=True)
        backup = folder / f"{path.stem}-v1-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.sqlite3"
        with sqlite3.connect(backup) as target:
            db.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Pre-migration backup failed integrity check")
    with db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "ALTER TABLE job_sources ADD COLUMN lifecycle_state TEXT NOT NULL DEFAULT 'unknown' CHECK(lifecycle_state IN ('active','closed','expired','unknown'))"
        )
        db.execute("ALTER TABLE job_sources ADD COLUMN last_checked_at TEXT")
        db.execute("ALTER TABLE job_sources ADD COLUMN closure_evidence TEXT NOT NULL DEFAULT '{}'")
        db.execute("ALTER TABLE job_sources ADD COLUMN seen_scan INTEGER")
        db.execute("""CREATE TABLE scans (
            id INTEGER PRIMARY KEY, source TEXT NOT NULL, board TEXT NOT NULL,
            started_at REAL NOT NULL, finished_at REAL, result TEXT NOT NULL DEFAULT 'interrupted',
            exhaustive INTEGER NOT NULL DEFAULT 0, fetched INTEGER NOT NULL DEFAULT 0)""")
        db.execute("CREATE TABLE host_cooldowns (host TEXT PRIMARY KEY, not_before REAL NOT NULL)")
        db.execute("""CREATE TABLE lifecycle_events (
            id INTEGER PRIMARY KEY, source TEXT NOT NULL, board TEXT NOT NULL, source_id TEXT NOT NULL,
            from_state TEXT, to_state TEXT NOT NULL, checked_at TEXT NOT NULL,
            evidence TEXT NOT NULL, scan_id INTEGER)""")
        db.execute("""CREATE TABLE company_boards (
            source TEXT NOT NULL, board TEXT NOT NULL, region TEXT NOT NULL DEFAULT 'global',
            company TEXT NOT NULL, discovery_url TEXT NOT NULL, board_url TEXT NOT NULL,
            verified_at TEXT, approved INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 0,
            evidence TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(source, board, region))""")
        db.execute(
            "CREATE TABLE source_overrides (source TEXT NOT NULL, board TEXT NOT NULL, enabled INTEGER NOT NULL, PRIMARY KEY(source,board))"
        )
        # Re-normalize from intact provider payloads, retaining timestamps and metadata.
        from .collectors import COLLECTORS
        from .config import SourceConfig
        from .models import Statistics

        for row in db.execute("SELECT source,board,source_id,payload FROM job_sources").fetchall():
            old = json.loads(row[3])
            kwargs = {"source": row[0]}
            if row[0] in {"greenhouse", "ashby", "lever"}:
                board = row[1]
                if row[0] == "lever":
                    region, board = board.split(":", 1) if ":" in board else ("global", board)
                    kwargs["region"] = region
                kwargs.update(board=board, company=old["company"])
            try:
                normalized = COLLECTORS[row[0]](
                    SourceConfig(**kwargs), None, Statistics(source=row[0])
                ).normalize(old["raw"])
                normalized.response_metadata = old.get("response_metadata", {})
                # Database identity is authoritative, including old board scopes.
                normalized.board, normalized.source_id = row[1], row[2]
                payload = normalized.model_dump_json()
            except (ValueError, TypeError, KeyError, AttributeError):
                # Preserve malformed legacy records byte-for-byte; unknown, not discarded.
                payload = row[3]
            db.execute(
                "UPDATE job_sources SET payload=? WHERE source=? AND board=? AND source_id=?",
                (payload, *row[:3]),
            )
        db.execute("DROP VIEW job_details")
        db.execute("""CREATE VIEW job_details AS
            SELECT j.id, j.first_seen, j.last_seen,
            j.first_seen AS first_seen_at, j.last_seen AS last_seen_at,
            (SELECT max(last_checked_at) FROM job_sources s WHERE s.job_id=j.id) AS last_checked_at,
            CASE WHEN EXISTS(SELECT 1 FROM job_sources s WHERE s.job_id=j.id AND lifecycle_state='active') THEN 'active'
                 WHEN EXISTS(SELECT 1 FROM job_sources s WHERE s.job_id=j.id AND lifecycle_state='unknown') THEN 'unknown'
                 WHEN EXISTS(SELECT 1 FROM job_sources s WHERE s.job_id=j.id AND lifecycle_state='closed') THEN 'closed'
                 ELSE 'expired' END AS status,
            json_extract(payload,'$.company') AS company,
            json_extract(payload,'$.title') AS title,
            json_extract(payload,'$.description') AS description,
            json_extract(payload,'$.location') AS location,
            json_extract(payload,'$.restrictions') AS restrictions,
            json_extract(payload,'$.employment_type') AS employment_type,
            json_extract(payload,'$.salary') AS salary,
            json_extract(payload,'$.workplace_type') AS workplace_type,
            json_extract(payload,'$.is_listed') AS is_listed,
            json_extract(payload,'$.posted_at') AS posted_at,
            json_extract(payload,'$.updated_at') AS updated_at,
            json_extract(payload,'$.expires_at') AS expires_at,
            json_extract(payload,'$.source_url') AS source_url,
            json_extract(payload,'$.application_url') AS application_url
            FROM jobs j""")
        db.execute("""CREATE VIEW discovery_jobs AS SELECT * FROM job_details
            WHERE status='active' AND is_listed=1
            AND (expires_at IS NULL OR julianday(expires_at)>julianday('now'))""")
        db.execute("PRAGMA user_version=2")
