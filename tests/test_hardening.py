import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from email.utils import format_datetime

import httpx
import pytest

from job_scraper.config import Settings, SourceConfig
from job_scraper.http import CollectionError, HttpClient
from job_scraper.models import Job, Statistics
from job_scraper.normalization import ashby_salary, greenhouse_salary, workplace
from job_scraper.runner import collect
from job_scraper.storage import SCHEMA, Store


@pytest.mark.parametrize("source", ["ashby", "lever"])
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Remote", "remote"),
        ("Hybrid", "hybrid"),
        ("On-Site", "onsite"),
        ("OnSite", "onsite"),
        ("unspecified", "unknown"),
        (None, "unknown"),
    ],
)
def test_workplace_classifications(source, raw, expected):
    assert workplace(source, {"workplaceType": raw, "isRemote": True}) == expected


@pytest.mark.parametrize(
    "source,raw,expected",
    [
        ("himalayas", {"locationRestrictions": ["US"]}, "remote"),
        ("remotive", {"candidate_required_location": "Pakistan"}, "remote"),
        ("arbeitnow", {"remote": True}, "remote"),
        ("arbeitnow", {"remote": False}, "unknown"),
        ("greenhouse", {"location": {"name": "Remote, US"}}, "unknown"),
    ],
)
def test_workplace_is_not_eligibility(source, raw, expected):
    assert workplace(source, raw) == expected


def test_greenhouse_date_regression(rows, make_collector):
    raw = {
        **rows["greenhouse"],
        "first_published": "2024-03-15T13:05:35-04:00",
        "application_deadline": "2000-01-01T00:00:00Z",
    }
    collector, _, _ = make_collector(
        "greenhouse", [{"jobs": [rows["greenhouse"]]}, raw], fetch_details=True
    )
    job = list(collector.collect())[0]
    assert job.posted_at.isoformat() == "2024-03-15T17:05:35+00:00"
    assert job.updated_at != job.posted_at
    assert job.raw["first_published"] == raw["first_published"]
    assert job.expires_at is None


@pytest.mark.parametrize("listed,count", [(True, 1), (False, 0)])
def test_unlisted_is_preserved_but_hidden(rows, make_collector, listed, count):
    collector, _, _ = make_collector("ashby", [{"jobs": [{**rows["ashby"], "isListed": listed}]}])
    with Store(":memory:") as store:
        for job in collector.collect():
            store.upsert(job)
        assert len(store.list_jobs()) == count
        assert len(store.list_jobs(include_history=True)) == 1


def test_compensation_components_not_conflated():
    raw = {
        "summaryComponents": [
            {
                "compensationType": "Salary",
                "minValue": 10,
                "maxValue": 20,
                "currencyCode": "EUR",
                "interval": "1 YEAR",
            },
            {"compensationType": "EquityPercentage", "minValue": 1, "maxValue": 5},
            {"compensationType": "Bonus", "minValue": 30, "maxValue": 40},
        ]
    }
    salary = ashby_salary(raw)
    assert (
        salary.minimum,
        salary.maximum,
        salary.currency,
        salary.period,
        salary.compensation_type,
    ) == (10, 20, "EUR", "annual", "base_salary")
    assert {c.compensation_type for c in salary.components} == {"base_salary", "bonus", "equity"}
    assert salary.details == raw
    assert ashby_salary({"summaryComponents": raw["summaryComponents"][1:]}).minimum is None


def test_ambiguous_salary_and_greenhouse_cents():
    raw = [{"min_cents": 5000000, "max_cents": 7500000, "currency_type": "USD"}]
    salary = greenhouse_salary(raw)
    assert (salary.minimum, salary.maximum, salary.currency, salary.period) == (
        50000,
        75000,
        "USD",
        None,
    )
    assert greenhouse_salary(raw * 2).minimum is None
    assert greenhouse_salary([{**raw[0], "title": "OTE range"}]).compensation_type == "unknown"


@pytest.mark.parametrize("date_header", [False, True])
def test_host_cooldown_survives_boards_and_transport_instances(date_header, tmp_path):
    mono, wall = [0.0], [1000.0]
    calls = []
    header = format_datetime(datetime.fromtimestamp(1120, UTC)) if date_header else "120"

    def respond(request):
        calls.append(request)
        return (
            httpx.Response(429, headers={"Retry-After": header})
            if len(calls) == 1
            else httpx.Response(200, json={})
        )

    path = tmp_path / "cooldown.sqlite3"
    with Store(path) as store, httpx.Client(transport=httpx.MockTransport(respond)) as client:
        http = HttpClient(
            client,
            retries=0,
            clock=lambda: mono[0],
            wall_clock=lambda: wall[0],
            cooldown_store=store,
        )
        with pytest.raises(CollectionError):
            http.get("https://api.test/board-one")
    with Store(path) as store, httpx.Client(transport=httpx.MockTransport(respond)) as client:
        http = HttpClient(
            client, clock=lambda: mono[0], wall_clock=lambda: wall[0], cooldown_store=store
        )
        with pytest.raises(CollectionError, match="deferred"):
            http.get("https://api.test/board-two")
        assert len(calls) == 1
        http.get("https://other.test/board")
        wall[0] = 1121
        http.get("https://api.test/board-two")
        assert len(calls) == 3


def sample(identifier="1", **kwargs):
    return Job(
        source="arbeitnow",
        source_id=identifier,
        company="Acme",
        title="Engineer",
        source_url=f"https://example.test/jobs/{identifier}",
        attribution="Arbeitnow",
        attribution_url="https://www.arbeitnow.com",
        **kwargs,
    )


def scan(store, jobs, stats=None, when=1000):
    sid = store.begin_scan("arbeitnow", "", now=when)
    for job in jobs:
        store.upsert(job, scan_id=sid)
    stats = stats or Statistics(source="arbeitnow", fetched=len(jobs))
    store.finish_scan(sid, stats, now=when, empty_confirmation_seconds=100)
    return stats


def test_exhaustive_closure_reopening_history_and_evidence():
    with Store(":memory:") as store:
        scan(store, [sample("1"), sample("2")])
        result = scan(store, [sample("1")], when=1100)
        assert result.closed == 1
        assert len(store.list_jobs()) == 1 and len(store.list_jobs(True)) == 2
        closed = store.db.execute(
            "SELECT closure_evidence FROM job_sources WHERE source_id='2'"
        ).fetchone()[0]
        assert json.loads(closed)["reason"] == "absent_from_exhaustive_scan"
        scan(store, [sample("1"), sample("2")], when=1200)
        assert len(store.list_jobs()) == 2
        assert [
            r[0]
            for r in store.db.execute(
                "SELECT to_state FROM lifecycle_events WHERE source_id='2' ORDER BY id"
            )
        ] == ["active", "closed", "active"]


@pytest.mark.parametrize(
    "failure", [{"errors": 1}, {"truncated": True}, {"invalid": 1}, {"skipped": True}]
)
def test_incomplete_scans_never_close(failure):
    with Store(":memory:") as store:
        scan(store, [sample("1"), sample("2")])
        stats = Statistics(source="arbeitnow", fetched=1, **failure)
        scan(store, [sample("1")], stats, when=1100)
        assert stats.closed == 0 and len(store.list_jobs()) == 2


def test_empty_confirmation_and_interrupted_scan():
    with Store(":memory:") as store:
        scan(store, [sample()])
        assert scan(store, [], when=1100).empty_confirmation_pending
        assert scan(store, [], when=1150).empty_confirmation_pending
        # An interrupted scan breaks the confirmation chain.
        store.begin_scan("arbeitnow", "", now=1200)
        assert scan(store, [], when=1300).empty_confirmation_pending
        assert len(store.list_jobs()) == 1
        assert scan(store, [], when=1400).closed == 1
        assert store.list_jobs() == []


def test_expiry_and_other_provider_still_active():
    with Store(":memory:") as store:
        result = store.upsert(sample(expires_at=datetime(2000, 1, 1, tzinfo=UTC)))
        assert result.expired and store.list_jobs() == []
        assert store.list_jobs(True)[0]["status"] == "expired"
        other = sample().model_copy(update={"source": "remotive"})
        store.upsert(other)
        assert len(store.list_jobs()) == 1


def test_source_not_checked_never_closed():
    with Store(":memory:") as store:
        store.upsert(sample().model_copy(update={"source": "remotive"}))
        scan(store, [sample("2")])
        assert len(store.list_jobs()) == 2


def test_migration_preserves_rows_raw_and_timestamps(tmp_path):
    path = tmp_path / "v1.sqlite3"
    payload = sample(raw={"custom": "preserve"}).model_dump_json()
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute("PRAGMA user_version=1")
        db.execute("INSERT INTO jobs VALUES (1,?,?,?)", (payload, "2020-01-01", "2021-01-01"))
        db.execute(
            "INSERT INTO job_sources VALUES ('arbeitnow','','1',1,?,?,?)",
            (payload, "2020-01-01", "2021-01-01"),
        )
    with Store(path) as store:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == 2
        row = store.db.execute("SELECT * FROM job_sources").fetchone()
        assert json.loads(row["payload"])["raw"] == {"custom": "preserve"}
        assert row["first_seen"] == "2020-01-01" and row["last_seen"] == "2021-01-01"
        assert row["lifecycle_state"] == "unknown"
        assert store.list_jobs() == []
    backups = list((tmp_path / "backups").glob("*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 1
    with Store(path) as store:
        assert len(store.list_jobs(True)) == 1


def test_single_writer_across_processes(tmp_path):
    path = tmp_path / "locked.sqlite3"
    command = [
        sys.executable,
        "-c",
        "from job_scraper.storage import Store; import sys; s=Store(sys.argv[1]); s.__exit__()",
        str(path),
    ]
    with Store(path):
        blocked = subprocess.run(command, capture_output=True, text=True)
        assert blocked.returncode != 0 and "Another writer" in blocked.stderr
    assert subprocess.run(command, capture_output=True).returncode == 0


def test_runner_interrupt_does_not_finalize(tmp_path, rows):
    settings = Settings(
        database=tmp_path / "interrupted.sqlite3", sources=[SourceConfig(source="arbeitnow")]
    )
    count = [0]

    def response(request):
        count[0] += 1
        if count[0] > 1:
            raise KeyboardInterrupt()
        return httpx.Response(200, json={"data": [rows["arbeitnow"]], "links": {"next": "?page=2"}})

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(KeyboardInterrupt):
            collect(settings, client=client, sleep=lambda _: None)
    with Store(settings.database) as store:
        assert store.db.execute("SELECT result FROM scans").fetchone()[0] == "interrupted"
        assert len(store.list_jobs()) == 1


def test_successful_response_can_defer_next_board():
    calls = []

    def response(request):
        calls.append(request)
        return httpx.Response(200, json={}, headers={"Retry-After": "120"})

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        http = HttpClient(client, clock=lambda: 0)
        assert http.get("https://example.test/one") == {}
        with pytest.raises(CollectionError, match="deferred"):
            http.get("https://example.test/two")
        assert len(calls) == 1


def test_visible_duplicate_not_hidden_by_unlisted_source():
    with Store(":memory:") as store:
        store.upsert(sample(description="Long description" * 50, is_listed=False))
        store.upsert(
            sample(description="Visible", is_listed=True).model_copy(update={"source": "ashby"})
        )
        assert store.list_jobs()[0]["description"] == "Visible"
        assert store.db.execute("SELECT count(*) FROM job_sources").fetchone()[0] == 2


def test_migration_failure_rolls_back_ddl(tmp_path, monkeypatch):
    from job_scraper.collectors import COLLECTORS

    path = tmp_path / "rollback.sqlite3"
    payload = sample(raw={"slug": "1"}).model_dump_json()
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute("PRAGMA user_version=1")
        db.execute("INSERT INTO jobs VALUES (1,?,?,?)", (payload, "2020", "2021"))
        db.execute(
            "INSERT INTO job_sources VALUES ('arbeitnow','','1',1,?,?,?)", (payload, "2020", "2021")
        )

    def broken(*args, **kwargs):
        raise RuntimeError("migration injection")

    monkeypatch.setitem(COLLECTORS, "arbeitnow", broken)
    with pytest.raises(RuntimeError, match="migration injection"):
        Store(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert "lifecycle_state" not in [r[1] for r in db.execute("PRAGMA table_info(job_sources)")]
        assert db.execute("SELECT payload FROM jobs").fetchone()[0] == payload


@pytest.mark.parametrize("board", [".", ".."])
def test_reject_board_path_segments(board):
    with pytest.raises(ValueError):
        SourceConfig(source="greenhouse", board=board, company="Acme")
