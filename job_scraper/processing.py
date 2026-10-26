"""Versioned screening persistence; source records remain untouched."""

import hashlib
import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from .models import Job
from .screening import CATEGORIES as CATEGORIES
from .screening import classify

VERSION = "pakistan-0-3-v3.1"


def ensure_schema(store):
    """An optional additive module schema; preserve collection schema/version."""
    current_exists = store.db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='job_classifications'"
    ).fetchone()
    history_exists = store.db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='job_classification_history'"
    ).fetchone()
    if current_exists and history_exists:
        return
    path = store.db.execute("PRAGMA database_list").fetchone()[2]
    if path:
        folder = Path(path).parent / "backups"
        folder.mkdir(exist_ok=True)
        backup = (
            folder
            / f"{Path(path).stem}-pre-processing-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.sqlite3"
        )
        with sqlite3.connect(backup) as target:
            store.db.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Processing backup failed integrity check")
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        store.db.execute("""CREATE TABLE IF NOT EXISTS job_classifications (
            job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
            rules_version TEXT NOT NULL, fingerprint TEXT NOT NULL,
            processed_at TEXT NOT NULL, result TEXT NOT NULL CHECK(json_valid(result)))""")
        # Historical job_id is intentionally not a foreign key: deduplication may
        # remove the old logical job, but source identities remain in result JSON.
        store.db.execute("""CREATE TABLE job_classification_history (
            id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL,
            rules_version TEXT NOT NULL, fingerprint TEXT NOT NULL,
            processed_at TEXT NOT NULL, result TEXT NOT NULL CHECK(json_valid(result)))""")
        store.db.execute("""INSERT INTO job_classification_history
            (job_id,rules_version,fingerprint,processed_at,result)
            SELECT job_id,rules_version,fingerprint,processed_at,result FROM job_classifications""")
        store.db.execute(
            "CREATE INDEX classification_history_job ON job_classification_history(job_id,id)"
        )
        for event in ("INSERT", "UPDATE"):
            store.db.execute(f"""CREATE TRIGGER classification_history_{event.lower()}
                AFTER {event} ON job_classifications BEGIN
                INSERT INTO job_classification_history(job_id,rules_version,fingerprint,processed_at,result)
                VALUES(NEW.job_id,NEW.rules_version,NEW.fingerprint,NEW.processed_at,NEW.result);
                END""")


def snapshots(store):
    for row in store.db.execute("SELECT id FROM jobs ORDER BY id"):
        records = [
            dict(r)
            for r in store.db.execute(
                "SELECT source,board,source_id,payload,lifecycle_state FROM job_sources WHERE job_id=? ORDER BY source,board,source_id",
                (row[0],),
            )
        ]
        # Observed-at times/page metadata do not change screening, content/state do.
        for record in records:
            payload = json.loads(record["payload"])
            payload.pop("response_metadata", None)
            record["payload"] = payload
        fingerprint = hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
        yield row[0], fingerprint, records


def process_jobs(store):
    ensure_schema(store)
    counts = {"processed": 0, "unchanged": 0, "ELIGIBLE": 0, "INELIGIBLE": 0, "UNVERIFIED": 0}
    with store.db:
        for job_id, fingerprint, records in snapshots(store):
            old = store.db.execute(
                "SELECT * FROM job_classifications WHERE job_id=?", (job_id,)
            ).fetchone()
            if old and old["fingerprint"] == fingerprint and old["rules_version"] == VERSION:
                counts["unchanged"] += 1
                counts[json.loads(old["result"])["status"]] += 1
                continue
            active = [
                r
                for r in records
                if r["lifecycle_state"] == "active" and r["payload"].get("is_listed", True)
            ]
            variants = []
            for record in active or records:
                try:
                    result = classify(Job.model_validate(record["payload"])).model_dump()
                except (ValidationError, ValueError, TypeError):
                    logging.getLogger(__name__).warning(
                        "Cannot classify legacy job %s from %s", job_id, record["source"]
                    )
                    result = {
                        "status": "UNVERIFIED",
                        "categories": [],
                        "error": "Invalid legacy Job payload",
                    }
                variants.append(
                    {
                        "source": record["source"],
                        "board": record["board"],
                        "source_id": record["source_id"],
                        **result,
                    }
                )
            states = {v["status"] for v in variants}
            status = next(iter(states)) if len(states) == 1 else "UNVERIFIED"
            result = {
                "status": status,
                "categories": sorted({c for v in variants for c in v["categories"]}),
                "variants": variants,
                "source_conflict": len(states) > 1,
                "profile": {
                    "working_from": "Pakistan",
                    "experience_years": [0, 3],
                    "citizenship": "unknown",
                    "foreign_work_authorization": "unknown",
                },
            }
            store.db.execute(
                """INSERT INTO job_classifications VALUES (?,?,?,?,?)
                ON CONFLICT(job_id) DO UPDATE SET rules_version=excluded.rules_version,
                fingerprint=excluded.fingerprint,processed_at=excluded.processed_at,result=excluded.result""",
                (job_id, VERSION, fingerprint, datetime.now(UTC).isoformat(), json.dumps(result)),
            )
            counts["processed"] += 1
            counts[status] += 1
    return counts


def inspect_results(store, *, status=None, category=None, job_id=None, include_history=False):
    ensure_schema(store)
    # Reuse the existing lifecycle/visibility policy. Never revive closed/unlisted jobs.
    visible = {r["id"]: r for r in store.list_jobs(include_history)}
    output = []
    for identity, fingerprint, _ in snapshots(store):
        if identity not in visible or (job_id is not None and job_id != identity):
            continue
        row = store.db.execute(
            "SELECT * FROM job_classifications WHERE job_id=?", (identity,)
        ).fetchone()
        stale = not row or row["fingerprint"] != fingerprint or row["rules_version"] != VERSION
        result = json.loads(row["result"]) if row else None
        effective = "UNVERIFIED" if stale else result["status"]
        if status and effective != status:
            continue
        if category and (stale or category not in result["categories"]):
            continue
        output.append(
            {
                "job_id": identity,
                "title": visible[identity]["title"],
                "company": visible[identity]["company"],
                "lifecycle": visible[identity]["status"],
                "source_url": visible[identity]["source_url"],
                "status": effective,
                "stale": stale,
                "processed_at": row["processed_at"] if row else None,
                "rules_version": row["rules_version"] if row else None,
                "classification": None if stale else result,
            }
        )
    return output
