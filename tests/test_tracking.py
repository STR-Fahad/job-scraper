import sqlite3
from datetime import UTC, datetime

import pytest
from test_storage import job

from job_scraper import tracking
from job_scraper.storage import Store

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def merge(store, keep, other):
    """What Store.upsert does when it discovers that two jobs are one."""
    store.db.execute("UPDATE job_sources SET job_id=? WHERE job_id=?", (keep, other))
    store.db.execute("UPDATE job_urls SET job_id=? WHERE job_id=?", (keep, other))
    tracking.repoint_after_merge(store.db, keep, other)
    store.db.execute("DELETE FROM jobs WHERE id=?", (other,))


def seeded(store, **changes):
    changes.setdefault("source_url", f"https://example.test/jobs/{changes.get('source_id', '1')}")
    store.upsert(job(**changes))
    return store.db.execute("SELECT max(id) FROM jobs").fetchone()[0]


def test_schema_is_additive_repeatable_and_backed_up(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    with Store(path) as store:
        job_id = seeded(store)
        before = store.db.execute("SELECT payload FROM jobs").fetchall()
        assert tracking.ensure_schema(store) is True
        assert tracking.ensure_schema(store) is False
        assert [r[0] for r in before] == [
            r[0] for r in store.db.execute("SELECT payload FROM jobs")
        ]
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert tracking.save_job(store, job_id, NOW)["created"] is True
    backups = list((tmp_path / "backups").glob("jobs-pre-tracking-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as old:
        assert old.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
        assert not tracking.has_schema(old)


def test_saving_creates_saved_not_applied_and_is_idempotent():
    with Store(":memory:") as store:
        job_id = seeded(store)
        first = tracking.save_job(store, job_id, NOW)
        assert (first["status"], first["applied_on"], first["created"]) == ("SAVED", None, True)
        again = tracking.save_job(store, job_id, NOW)
        assert (again["created"], again["id"]) == (False, first["id"])
        assert store.db.execute("SELECT count(*) FROM job_applications").fetchone()[0] == 1
        assert [h["event"] for h in tracking.history(store.db, job_id)] == ["created"]


def test_status_changes_are_recorded_and_applied_date_rules():
    with Store(":memory:") as store:
        job_id = seeded(store)
        tracking.save_job(store, job_id, NOW)
        planned = tracking.set_status(store, job_id, "PLANNED", now=NOW)
        assert planned["applied_on"] is None
        applied = tracking.set_status(store, job_id, "APPLIED", now=NOW)
        assert applied["applied_on"] == "2026-11-04"
        tracking.set_status(store, job_id, "INTERVIEW", note="phone screen booked", now=NOW)
        rows = tracking.history(store.db, job_id)
        assert [(h["from_status"], h["to_status"]) for h in rows] == [
            (None, "SAVED"),
            ("SAVED", "PLANNED"),
            ("PLANNED", "APPLIED"),
            ("APPLIED", "INTERVIEW"),
        ]
        assert rows[-1]["summary"] == "phone screen booked"
        unchanged = tracking.set_status(store, job_id, "INTERVIEW", now=NOW)
        assert unchanged["changed"] is False
        assert len(tracking.history(store.db, job_id)) == 4


def test_jump_to_sent_status_needs_a_date_and_creates_the_record():
    with Store(":memory:") as store:
        job_id = seeded(store)
        with pytest.raises(ValueError, match="needs the date you applied"):
            tracking.set_status(store, job_id, "INTERVIEW", now=NOW)
        assert tracking.get(store.db, job_id) is None
        made = tracking.set_status(store, job_id, "INTERVIEW", applied_on="2026-10-30", now=NOW)
        assert made["applied_on"] == "2026-10-30"
        rejected = tracking.set_status(store, job_id, "REJECTED", now=NOW)
        assert rejected["applied_on"] == "2026-10-30"


@pytest.mark.parametrize(
    "value",
    ["2026-13-01", "2026-02-30", "04/11/2026", "20261104", "2026-11-4", "yesterday", "2030-01-01"],
)
def test_invalid_applied_dates_are_rejected(value):
    with Store(":memory:") as store:
        job_id = seeded(store)
        with pytest.raises(ValueError, match="date applied"):
            tracking.set_status(store, job_id, "APPLIED", applied_on=value, now=NOW)
        assert tracking.get(store.db, job_id) is None


def test_details_are_validated_and_notes_stay_out_of_history():
    with Store(":memory:") as store:
        job_id = seeded(store)
        with pytest.raises(ValueError, match="Save the job"):
            tracking.update_details(store, job_id, notes="x", now=NOW)
        tracking.save_job(store, job_id, NOW)
        done = tracking.update_details(
            store,
            job_id,
            follow_up_on="2026-11-20",
            channel="company site",
            reference="https://example.com/apply/1",
            notes="secret salary expectation 123",
            now=NOW,
        )
        assert done["follow_up_on"] == "2026-11-20" and done["notes"].startswith("secret")
        rows = tracking.history(store.db, job_id)
        assert rows[-1]["event"] == "details_updated"
        assert "secret" not in str(rows) and "123" not in rows[-1]["summary"]
        assert (
            tracking.update_details(store, job_id, notes="secret salary expectation 123")["changed"]
            is False
        )
        for bad in (
            {"follow_up_on": "2026-02-31"},
            {"follow_up_on": "2020-01-01"},
            {"reference": "javascript:alert(1)"},
            {"reference": "file:///etc/passwd"},
            {"reference": "https://"},
            {"notes": "x" * 5001},
            {"channel": "y" * 101},
            {"notes": "bad\x00text"},
        ):
            with pytest.raises(ValueError):
                tracking.update_details(store, job_id, now=NOW, **bad)
        assert tracking.get(store.db, job_id)["notes"].startswith("secret")
        assert (
            tracking.update_details(store, job_id, reference="Recruiter email, ref 42")["reference"]
            == "Recruiter email, ref 42"
        )


def test_invalid_job_ids_and_statuses_are_refused():
    with Store(":memory:") as store:
        for bad in (999, 0, -1, True, "3", None):
            with pytest.raises(ValueError):
                tracking.save_job(store, bad, NOW)
        job_id = seeded(store)
        with pytest.raises(ValueError, match="status must be one of"):
            tracking.set_status(store, job_id, "HIRED", now=NOW)
        assert tracking.counts(store.db)["total"] == 0


def test_database_refuses_a_dangling_job_and_rewriting_history():
    with Store(":memory:") as store:
        job_id = seeded(store)
        tracking.save_job(store, job_id, NOW)
        with pytest.raises(sqlite3.IntegrityError, match="existing job"):
            with store.db:
                store.db.execute(
                    "INSERT INTO job_applications(job_id,status,saved_at,updated_at) "
                    "VALUES (404,'SAVED','t','t')"
                )
        for statement in (
            "UPDATE job_application_history SET summary='x'",
            "DELETE FROM job_application_history",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                with store.db:
                    store.db.execute(statement)
        with pytest.raises(sqlite3.IntegrityError):
            with store.db:
                store.db.execute("UPDATE job_applications SET status='APPLIED', applied_on=NULL")


def test_failed_write_leaves_no_partial_state(monkeypatch):
    with Store(":memory:") as store:
        job_id = seeded(store)
        tracking.save_job(store, job_id, NOW)

        def boom(*args, **kwargs):
            raise RuntimeError("history write failed")

        monkeypatch.setattr(tracking, "_event", boom)
        with pytest.raises(RuntimeError):
            tracking.set_status(store, job_id, "PLANNED", now=NOW)
        assert tracking.get(store.db, job_id)["status"] == "SAVED"
        assert len(tracking.history(store.db, job_id)) == 1


def test_persists_across_restart_and_survives_a_closed_job(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    with Store(path) as store:
        job_id = seeded(store)
        tracking.set_status(store, job_id, "APPLIED", applied_on="2026-11-01", now=NOW)
        tracking.update_details(store, job_id, notes="kept", now=NOW)
    with Store(path) as store:
        record = tracking.get(store.db, job_id)
        assert (record["status"], record["notes"]) == ("APPLIED", "kept")
        store.db.execute("UPDATE job_sources SET lifecycle_state='closed'")
        store.db.commit()
        store._refresh_job(job_id)
        listing = tracking.list_applications(store.db)
        assert listing["total"] == 1
        assert listing["items"][0]["job_status"] == "closed"
        assert listing["items"][0]["status"] == "APPLIED"
        assert tracking.orphans(store.db) == []


def test_counts_listing_filters_and_search():
    with Store(":memory:") as store:
        a = seeded(store, source_id="1", title="Python Engineer", company="Acme")
        b = seeded(store, source_id="2", title="Rails Developer", company="Globex")
        c = seeded(store, source_id="3", title="Data Analyst", company="Initech")
        tracking.save_job(store, a, NOW)
        tracking.set_status(store, b, "APPLIED", now=NOW)
        tracking.set_status(store, c, "OFFER", applied_on="2026-10-01", now=NOW)
        stats = tracking.counts(store.db)
        assert stats["total"] == 3 and stats["submitted"] == 2 and stats["in_progress"] == 1
        assert stats["by_status"]["OFFER"] == 1 and stats["by_status"]["SAVED"] == 1
        assert tracking.list_applications(store.db, status="APPLIED")["items"][0]["company"] == (
            "Globex"
        )
        assert tracking.list_applications(store.db, query="python")["total"] == 1
        with pytest.raises(ValueError):
            tracking.list_applications(store.db, status="NOPE")
        assert tracking.list_applications(store.db, limit=1, offset=1)["items"].__len__() == 1


def test_merging_duplicate_jobs_keeps_the_application():
    with Store(":memory:") as store:
        keep = seeded(store, source_id="1", source="greenhouse", title="Engineer")
        other = seeded(store, source_id="2", source="greenhouse", title="Engineer")
        tracking.save_job(store, other, NOW)
        merge(store, keep, other)
        assert tracking.get(store.db, keep)["status"] == "SAVED"
        assert tracking.orphans(store.db) == []
        assert len(tracking.history(store.db, keep)) == 1


def test_orphan_is_reported_when_both_jobs_were_tracked():
    with Store(":memory:") as store:
        keep = seeded(store, source_id="1", title="Engineer")
        other = seeded(store, source_id="2", title="Engineer")
        tracking.save_job(store, keep, NOW)
        tracking.save_job(store, other, NOW)
        merge(store, keep, other)
        assert tracking.orphans(store.db) == [other]
        assert tracking.counts(store.db)["total"] == 2


def test_safe_link_accepts_only_web_urls():
    assert tracking.safe_link("https://example.com/a?b=1") == "https://example.com/a?b=1"
    for bad in ("javascript:alert(1)", "data:text/html,x", "ftp://x.test", "//x.test", "", None):
        assert tracking.safe_link(bad) is None


def test_real_upsert_merge_keeps_the_user_application(tmp_path):
    """Store.upsert deletes the merged job row; the application must follow the survivor."""
    shared = "https://jobs.lever.co/acme/0123456789abcdef0123456789abcdef"
    with Store(tmp_path / "jobs.sqlite3") as store:
        first = seeded(store, source_id="1", source_url="https://example.test/jobs/a")
        second = seeded(
            store,
            source="greenhouse",
            source_id="2",
            source_url="https://example.test/jobs/b",
            application_url=shared + "/apply",
        )
        assert first != second
        tracking.set_status(store, second, "APPLIED", now=NOW)
        # A third observation links both jobs: one canonical URL points to each of them.
        store.upsert(
            job(
                source="greenhouse",
                source_id="3",
                source_url="https://example.test/jobs/a",
                application_url=shared + "/apply",
            )
        )
        survivors = [r[0] for r in store.db.execute("SELECT id FROM jobs")]
        assert survivors == [first]
        record = tracking.get(store.db, first)
        assert record is not None and record["status"] == "APPLIED"
        assert tracking.orphans(store.db) == []
        assert len(tracking.history(store.db, first)) == 1
