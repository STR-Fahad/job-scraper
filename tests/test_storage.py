import json
import sqlite3

import pytest

from job_scraper.models import Job, canonical_url
from job_scraper.storage import Store


def job(**changes):
    data = dict(
        source="greenhouse",
        board="acme",
        source_id="1",
        company="Acme",
        title="Engineer",
        description="Description",
        source_url="https://example.test/jobs/1",
        attribution="Greenhouse",
        attribution_url="https://www.greenhouse.com",
    )
    data.update(changes)
    return Job(**data)


def count(store, table="jobs"):
    assert table in {"jobs", "job_sources", "job_urls"}
    return store.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_repeat_upsert_and_changed_fields(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    with Store(path) as store:
        assert store.upsert(job()).action == "inserted"
        assert store.upsert(job()).action == "unchanged"
        assert store.upsert(job(title="Senior Engineer")).action == "updated"
        assert count(store) == count(store, "job_sources") == 1
        assert store.db.execute("SELECT title FROM job_details").fetchone()[0] == "Senior Engineer"
    with Store(path) as store:
        assert store.upsert(job(title="Senior Engineer")).action == "unchanged"


def test_source_ids_are_scoped():
    with Store(":memory:") as store:
        store.upsert(job())
        store.upsert(job(board="other", source_url="https://example.test/jobs/2"))
        store.upsert(job(source="ashby", source_url="https://example.test/jobs/3"))
        assert count(store) == 3


def test_canonical_url_duplicate_retains_both_metadata():
    with Store(":memory:") as store:
        store.upsert(job(raw={"custom": "original"}))
        result = store.upsert(
            job(
                source="ashby",
                source_id="different",
                source_url="https://example.test/jobs/1?utm_source=feed#description",
                raw={"custom": "second"},
            )
        )
        assert result.duplicate
        assert count(store) == 1 and count(store, "job_sources") == 2
        assert {
            json.loads(r[0])["raw"]["custom"]
            for r in store.db.execute("SELECT payload FROM job_sources")
        } == {"original", "second"}


def test_later_evidence_merges_existing_rows():
    with Store(":memory:") as store:
        store.upsert(job())
        store.upsert(job(source_id="2", source_url="https://example.test/jobs/2"))
        result = store.upsert(job(source_url="https://example.test/jobs/2"))
        assert result.merged == 1
        assert count(store) == 1 and count(store, "job_sources") == 2
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert store.upsert(job(source_id="3")).duplicate  # old URL retained as alias


def test_shared_application_url_does_not_merge_distinct_jobs():
    with Store(":memory:") as store:
        store.upsert(job(application_url="https://example.test/apply"))
        store.upsert(
            job(
                source_id="2",
                source_url="https://example.test/jobs/2",
                application_url="https://example.test/apply",
            )
        )
        assert count(store) == 2


def test_known_ats_application_url_cross_source_match():
    ats_url = "https://jobs.lever.co/acme/12345678-1234-1234-1234-123456789abc"
    with Store(":memory:") as store:
        store.upsert(job(source="lever", source_url=ats_url))
        result = store.upsert(
            job(
                source="himalayas",
                source_url="https://himalayas.app/companies/acme/jobs/1",
                application_url=ats_url + "/apply?utm_source=feed",
            )
        )
        assert result.duplicate and count(store) == 1
        # Never replace the provider-required application link with its identity URL.
        payload = json.loads(
            store.db.execute("SELECT payload FROM job_sources WHERE source='himalayas'").fetchone()[
                0
            ]
        )
        assert payload["application_url"].endswith("/apply?utm_source=feed")


def test_transaction_rolls_back_on_failure():
    with Store(":memory:") as store:
        store.db.execute(
            "CREATE TRIGGER reject_source BEFORE INSERT ON job_sources BEGIN SELECT RAISE(ABORT, 'test'); END"
        )
        with pytest.raises(sqlite3.IntegrityError):
            store.upsert(job())
        assert count(store) == 0


def test_cooldown_persists_across_connections(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    with Store(path) as store:
        assert store.claim_run("remotive", "", 21600, now=100)
    with Store(path) as store:
        assert not store.claim_run("remotive", "", 21600, now=101)
        assert store.claim_run("remotive", "", 21600, now=21700)


def test_canonicalization_keeps_identity_query_and_path_case():
    assert (
        canonical_url("https://EXAMPLE.test/Jobs?id=2&utm_source=x#top")
        == "https://example.test/Jobs?id=2"
    )
    assert canonical_url("https://example.test/jobs?id=1") != canonical_url(
        "https://example.test/jobs?id=2"
    )
    assert canonical_url("https://example.test/#/jobs/1").endswith("#/jobs/1")


def test_reject_future_schema(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=999")
    with pytest.raises(ValueError, match="Unsupported"):
        Store(path)
