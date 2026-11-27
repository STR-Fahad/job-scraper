"""Link check: evidence rules, dry run, and the two-checks-on-different-days rule."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_storage import job

from job_scraper import linkcheck
from job_scraper.http import Probe
from job_scraper.linkcheck import LIVE, REMOVED, UNKNOWN, classify, decide_close, usable_url
from job_scraper.storage import Store

ORIGINAL_PRIORITIES = linkcheck._priorities  # the autouse fixture replaces it in every test
URL = "https://boards.greenhouse.io/acme/jobs/123"
DAY1 = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)


def cls(status, source="greenhouse", url=URL, **kw):
    return classify(source, "123", url, Probe(status, **kw))


# ---- evidence rules -------------------------------------------------------------------------


@pytest.mark.parametrize("status", [404, 410])
def test_gone_status_codes_are_removed(status):
    check = cls(status)
    assert check.verdict == REMOVED and check.reason == f"http_{status}"


@pytest.mark.parametrize("status", [500, 502, 503, 429, 401, 403, 418, 408])
def test_server_errors_and_blocks_are_unknown_never_removed(status):
    assert cls(status).verdict == UNKNOWN


def test_timeouts_and_transport_errors_are_unknown():
    check = cls(None, error="ReadTimeout")
    assert check.verdict == UNKNOWN and check.reason == "ReadTimeout"
    assert cls(None, error="host_deferred").verdict == UNKNOWN


def test_redirect_to_the_board_page_is_removed():
    assert cls(302, location="/acme").verdict == REMOVED
    assert cls(301, location="https://boards.greenhouse.io/acme/jobs").verdict == REMOVED
    assert cls(302, location="/acme/jobs/123?error=true").verdict == REMOVED
    assert cls(302, location="/").verdict == REMOVED


def test_other_redirects_are_unknown():
    assert cls(302, location="https://login.example.com/sso").verdict == UNKNOWN
    assert cls(302, location="/other/place/entirely").verdict == UNKNOWN
    assert cls(302, location="/acme/jobs/456").verdict == UNKNOWN  # a different posting
    assert cls(302).verdict == UNKNOWN  # no Location header


def test_closed_marker_on_a_200_page_is_removed_and_a_normal_page_is_live():
    assert cls(200, text="Oops. THIS JOB IS NO LONGER AVAILABLE.").verdict == REMOVED
    assert (
        cls(200, source="greenhouse", text="The job you are looking for is no longer open.").reason
        == "closed_marker"
    )
    assert cls(200, source="lever", text="Sorry, we couldn't find anything here").verdict == REMOVED
    assert cls(200, text="Senior Engineer. Apply now.").verdict == LIVE
    # a source's own phrase does not apply to another source
    assert cls(200, source="remotive", text="Sorry, we couldn't find anything here").verdict == LIVE


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/jobs/1",
        "http://127.0.0.1/jobs/1",
        "http://10.0.0.5/jobs/1",
        "http://192.168.1.2/x",
        "http://[::1]/x",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "https://printer.local/x",
        "https://nodots/x",
        "",
        None,
    ],
)
def test_unusable_urls_are_refused(url):
    assert usable_url(url) is False


def test_public_urls_are_usable():
    assert usable_url("https://boards.greenhouse.io/acme/jobs/1") is True


# ---- the two-check rule ---------------------------------------------------------------------


def seen(verdict, when):
    return {"verdict": verdict, "checked_at": when.isoformat(), "reason": "x"}


def test_one_removed_check_never_closes():
    assert decide_close([], REMOVED, DAY1) is None
    assert decide_close([seen(REMOVED, DAY1 - timedelta(days=2))], LIVE, DAY1) is None
    assert decide_close([seen(REMOVED, DAY1 - timedelta(days=2))], UNKNOWN, DAY1) is None


def test_two_removed_checks_on_different_days_close():
    earlier = seen(REMOVED, DAY1 - timedelta(days=1))
    assert decide_close([earlier], REMOVED, DAY1) == [earlier]


def test_two_removed_checks_on_the_same_day_do_not_close():
    assert decide_close([seen(REMOVED, DAY1 - timedelta(hours=3))], REMOVED, DAY1) is None


def test_different_calendar_day_but_under_twelve_hours_does_not_close():
    late = datetime(2026, 11, 23, 23, 50, tzinfo=UTC)
    assert (
        decide_close([seen(REMOVED, late)], REMOVED, datetime(2026, 11, 24, 0, 10, tzinfo=UTC))
        is None
    )


def test_a_live_check_in_between_restarts_the_count_and_unknown_is_ignored():
    removed = seen(REMOVED, DAY1 - timedelta(days=3))
    live = seen(LIVE, DAY1 - timedelta(days=2))
    unknown = seen(UNKNOWN, DAY1 - timedelta(days=1))
    assert decide_close([removed, live], REMOVED, DAY1) is None
    assert decide_close([removed, unknown], REMOVED, DAY1) == [removed]


# ---- a fake network and a small database -----------------------------------------------------


class FakeHttp:
    """HttpClient stand-in: `pages` maps a URL to a Probe (default 200 live) and logs requests."""

    def __init__(self, pages=None, robots=None):
        self.pages = pages or {}
        self.robots = robots
        self.requests = []

    def probe(self, url, *, headers=None, interval=2, max_chars=200_000):
        self.requests.append(url)
        if url.endswith("/robots.txt"):
            return self.robots or Probe(404)
        return self.pages.get(url, Probe(200, text="Open role"))


def build(tmp_path, count=3):
    path = tmp_path / "data" / "jobs.sqlite3"
    with Store(path) as store:
        for number in range(1, count + 1):
            store.upsert(
                job(
                    source_id=str(number),
                    title=f"Engineer {number}",
                    source_url=f"https://boards.greenhouse.io/acme/jobs/{number}",
                )
            )
    settings = SimpleNamespace(
        database=path, matching=None, timeout_seconds=5, retries=0, max_retry_delay=1
    )
    return path, settings


@pytest.fixture(autouse=True)
def no_ranking(monkeypatch):
    monkeypatch.setattr(linkcheck, "_priorities", lambda db, config, path: ({}, False))


def rows(path, sql):
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(path)) as db:
        return db.execute(sql).fetchall()


def states(path):
    return dict(rows(path, "SELECT source_id, lifecycle_state FROM job_sources"))


def link_events(path):
    return rows(
        path,
        "SELECT source_id, from_state, to_state, evidence FROM lifecycle_events WHERE evidence LIKE '%link_check%'",
    )


def gone(number):
    return f"https://boards.greenhouse.io/acme/jobs/{number}"


# ---- dry run ---------------------------------------------------------------------------------


def test_dry_run_checks_but_writes_nothing(tmp_path):
    path, settings = build(tmp_path)
    before = rows(path, "SELECT * FROM lifecycle_events")
    http = FakeHttp({gone(1): Probe(404), gone(2): Probe(503)})
    report = linkcheck.check_links(settings, http=http, now=DAY1)
    assert report["mode"] == "dry-run"
    assert report["totals"]["checked"] == 3
    assert report["totals"]["removed_first_check"] == 1
    assert report["totals"]["unknown"] == 1 and report["totals"]["live"] == 1
    assert report["by_source"]["greenhouse"]["checked"] == 3
    assert report["unknown_reasons"] == {"http_503": 1}
    assert report["removed"][0]["action"] == "would record the first removed check"
    assert rows(path, "SELECT * FROM lifecycle_events") == before
    assert set(states(path).values()) == {"active"}
    assert not list(path.parent.glob("backups/jobs-pre-linkcheck-*"))
    assert "Dry run: nothing was written" in linkcheck.render(report)


def test_dry_run_works_while_another_program_holds_the_writer_lock(tmp_path):
    path, settings = build(tmp_path, count=1)
    with Store(path):  # the writer lock is held for the whole check
        report = linkcheck.check_links(settings, http=FakeHttp(), now=DAY1)
    assert report["totals"]["live"] == 1


def test_limit_and_priority_order(tmp_path, monkeypatch):
    path, settings = build(tmp_path, count=3)
    ids = dict(rows(path, "SELECT source_id, job_id FROM job_sources"))
    monkeypatch.setattr(
        linkcheck,
        "_priorities",
        lambda db, config, path: ({ids["3"]: 0, ids["2"]: 1, ids["1"]: 2}, True),
    )
    http = FakeHttp()
    report = linkcheck.check_links(settings, http=http, now=DAY1, limit=2)
    posted = [u for u in http.requests if "/jobs/" in u]
    assert posted == [gone(3), gone(2)]  # Eligible first, then Unverified; the rest not reached
    assert report["not_checked_this_run"] == 1


def test_robots_disallow_and_unreadable_robots_are_unknown(tmp_path):
    path, settings = build(tmp_path, count=1)
    blocked = FakeHttp(robots=Probe(200, text="User-agent: *\nDisallow: /acme/"))
    report = linkcheck.check_links(settings, http=blocked, now=DAY1)
    assert report["unknown_reasons"] == {"disallowed_by_robots": 1}
    assert [u for u in blocked.requests if "/jobs/" in u] == []
    broken = FakeHttp(robots=Probe(500))
    report = linkcheck.check_links(settings, http=broken, now=DAY1)
    assert report["unknown_reasons"] == {"robots_unreadable": 1}


def test_same_posting_redirect_is_followed(tmp_path):
    path, settings = build(tmp_path, count=1)
    http = FakeHttp(
        {gone(1): Probe(301, location=gone(1) + "/"), gone(1) + "/": Probe(200, text="Open")}
    )
    assert linkcheck.check_links(settings, http=http, now=DAY1)["totals"]["live"] == 1


def test_a_private_stored_url_is_never_requested(tmp_path):
    path = tmp_path / "data" / "jobs.sqlite3"
    with Store(path) as store:
        store.upsert(job(source_url="http://127.0.0.1:8765/secret"))
    settings = SimpleNamespace(
        database=path, matching=None, timeout_seconds=5, retries=0, max_retry_delay=1
    )
    http = FakeHttp()
    report = linkcheck.check_links(settings, http=http, now=DAY1)
    assert http.requests == []
    assert report["unknown_reasons"] == {"unusable_url": 1}


# ---- apply: events, backup, and the two-check rule ---------------------------------------------


def test_first_removed_check_is_recorded_but_does_not_close(tmp_path):
    path, settings = build(tmp_path)
    http = FakeHttp({gone(1): Probe(404)})
    report = linkcheck.check_links(settings, apply=True, http=http, now=DAY1)
    assert report["mode"] == "apply" and report["closed_written"] == 0
    assert set(states(path).values()) == {"active"}
    events = link_events(path)
    assert len(events) == 3  # every result is a lifecycle event
    first = next(e for e in events if e[0] == "1")
    assert first[1] == first[2] == "active"
    evidence = json.loads(first[3])
    assert evidence["verdict"] == REMOVED and evidence["reason"] == "http_404"
    assert evidence["http_status"] == 404 and evidence["kind"] == "link_check"
    # nothing closed, so no backup is made
    assert not list((path.parent / "backups").glob("jobs-pre-linkcheck-*.sqlite3"))


def test_two_removed_checks_on_different_days_close_and_history_is_kept(tmp_path):
    path, settings = build(tmp_path)
    http = FakeHttp({gone(1): Probe(404)})
    linkcheck.check_links(settings, apply=True, http=http, now=DAY1)
    # the same day: sources checked within 12 hours are skipped, so no second check happens
    again = linkcheck.check_links(settings, apply=True, http=http, now=DAY1 + timedelta(hours=3))
    assert again["skipped_checked_within_12h"] == 3 and again["totals"] == {}
    assert states(path)["1"] == "active"
    day2 = linkcheck.check_links(settings, apply=True, http=http, now=DAY1 + timedelta(days=1))
    assert day2["closed_written"] == 1 and day2["backup"]  # a closing run is backed up
    assert states(path) == {"1": "closed", "2": "active", "3": "active"}
    closed = rows(path, "SELECT closure_evidence FROM job_sources WHERE source_id='1'")[0][0]
    proof = json.loads(closed)
    assert proof["reason"] == "link_check_removed_twice" and len(proof["checks"]) == 2
    transitions = [(e[1], e[2]) for e in link_events(path) if e[0] == "1"]
    assert ("active", "closed") in transitions
    # nothing deleted: the job and its source are still stored and the closed job leaves the list
    assert rows(path, "SELECT count(*) FROM jobs")[0][0] == 3
    with Store(path) as store:
        assert len(store.list_jobs()) == 2


def test_dry_run_reports_would_close_on_the_second_day(tmp_path):
    path, settings = build(tmp_path, count=1)
    http = FakeHttp({gone(1): Probe(404)})
    linkcheck.check_links(settings, apply=True, http=http, now=DAY1)
    report = linkcheck.check_links(settings, http=http, now=DAY1 + timedelta(days=1))
    assert report["removed"][0]["action"] == "would close"
    assert states(path)["1"] == "active"  # a dry run never closes


def test_a_live_check_between_two_removed_checks_prevents_closing(tmp_path):
    path, settings = build(tmp_path, count=1)
    removed, live = FakeHttp({gone(1): Probe(404)}), FakeHttp()
    linkcheck.check_links(settings, apply=True, http=removed, now=DAY1)
    linkcheck.check_links(settings, apply=True, http=live, now=DAY1 + timedelta(days=1))
    linkcheck.check_links(settings, apply=True, http=removed, now=DAY1 + timedelta(days=2))
    assert states(path)["1"] == "active"
    linkcheck.check_links(settings, apply=True, http=removed, now=DAY1 + timedelta(days=3))
    assert states(path)["1"] == "closed"


def test_unknown_results_never_close_however_often_they_repeat(tmp_path):
    path, settings = build(tmp_path, count=1)
    http = FakeHttp({gone(1): Probe(503)})
    for day in range(4):
        linkcheck.check_links(settings, apply=True, http=http, now=DAY1 + timedelta(days=day))
    assert states(path)["1"] == "active"
    assert len(link_events(path)) == 4


def test_apply_with_nothing_to_check_makes_no_backup(tmp_path):
    path = tmp_path / "data" / "jobs.sqlite3"
    with Store(path):
        pass
    settings = SimpleNamespace(
        database=path, matching=None, timeout_seconds=5, retries=0, max_retry_delay=1
    )
    report = linkcheck.check_links(settings, apply=True, http=FakeHttp(), now=DAY1)
    assert report["totals"] == {} and report["backup"] == ""
    assert not list(path.parent.glob("backups/jobs-pre-linkcheck-*"))


def test_a_closed_source_reopens_when_the_feed_lists_it_again(tmp_path):
    path, settings = build(tmp_path, count=1)
    http = FakeHttp({gone(1): Probe(404)})
    linkcheck.check_links(settings, apply=True, http=http, now=DAY1)
    linkcheck.check_links(settings, apply=True, http=http, now=DAY1 + timedelta(days=1))
    assert states(path)["1"] == "closed"
    with Store(path) as store:
        store.upsert(job(source_id="1", source_url=gone(1)))
    assert states(path)["1"] == "active"


def test_every_source_of_a_job_is_checked_separately(tmp_path):
    path = tmp_path / "data" / "jobs.sqlite3"
    with Store(path) as store:
        store.upsert(job(source_id="1", source_url=gone(1)))
        store.upsert(
            job(
                source="ashby",
                board="acme",
                source_id="9",
                source_url="https://jobs.ashbyhq.com/acme/9",
                title="Engineer",
                company="Acme",
            )
        )
    settings = SimpleNamespace(
        database=path, matching=None, timeout_seconds=5, retries=0, max_retry_delay=1
    )
    http = FakeHttp({gone(1): Probe(404)})
    report = linkcheck.check_links(settings, http=http, now=DAY1)
    assert set(report["by_source"]) <= {"greenhouse", "ashby"}
    assert sum(r["checked"] for r in report["by_source"].values()) == report["totals"]["checked"]


def test_cli_dry_run_by_default(tmp_path, capsys, monkeypatch):
    from job_scraper import cli

    config = tmp_path / "config.toml"
    config.write_text('[[sources]]\nsource="arbeitnow"', encoding="utf-8")
    seen = {}

    def fake(settings, **kwargs):
        seen.update(kwargs)
        return {
            "mode": "apply" if kwargs["apply"] else "dry-run",
            "candidates": 0,
            "skipped_checked_within_12h": 0,
            "skipped_ineligible_or_outside_pool": 0,
            "limit": kwargs["limit"],
            "not_checked_this_run": 0,
            "totals": {},
            "by_source": {},
            "removed": [],
            "unknown_reasons": {},
            "closed_written": 0,
            "backup": "",
        }

    monkeypatch.setattr(cli, "check_links", fake)
    assert cli.main(["check-links", "--config", str(config)]) == 0
    assert seen["apply"] is False and seen["limit"] == 100
    assert "Link check (dry-run)" in capsys.readouterr().out
    assert cli.main(["check-links", "--config", str(config), "--apply", "--limit", "5"]) == 0
    assert seen["apply"] is True and seen["limit"] == 5


def test_priority_order_falls_back_when_the_writer_lock_is_busy(tmp_path, monkeypatch):
    monkeypatch.setattr(linkcheck, "PRIORITY_TRIES", 1)
    import sqlite3
    from contextlib import closing

    path, settings = build(tmp_path, count=1)
    with Store(path), closing(sqlite3.connect(path)) as db:
        assert ORIGINAL_PRIORITIES(db, settings.matching, path) == (
            {},
            False,
        )  # lock busy, none judged
    # with no lock the ranking itself is attempted; a bad config must not stop the check
    with closing(sqlite3.connect(path)) as db:
        assert ORIGINAL_PRIORITIES(db, None, path) == ({}, False)


def test_ineligible_and_outside_pool_jobs_are_not_checked(tmp_path, monkeypatch):
    path, settings = build(tmp_path, count=4)
    ids = dict(rows(path, "SELECT source_id, job_id FROM job_sources"))
    # 1 Eligible, 2 Unverified, 3 Ineligible, 4 outside the judging pool (not in the dict)
    tiers = {ids["1"]: 0, ids["2"]: 1, ids["3"]: 3}
    monkeypatch.setattr(linkcheck, "_priorities", lambda db, config, path: (tiers, True))
    http = FakeHttp()
    report = linkcheck.check_links(settings, http=http, now=DAY1)
    assert sorted(u for u in http.requests if "/jobs/" in u) == [gone(1), gone(2)]
    assert report["skipped_ineligible_or_outside_pool"] == 2
    assert report["totals"]["checked"] == 2
    everything = FakeHttp()
    report = linkcheck.check_links(settings, http=everything, now=DAY1, include_ineligible=True)
    assert report["totals"]["checked"] == 4 and report["skipped_ineligible_or_outside_pool"] == 0


def test_when_the_ranking_is_unreadable_nothing_is_skipped(tmp_path, monkeypatch):
    path, settings = build(tmp_path, count=2)
    ids = dict(rows(path, "SELECT source_id, job_id FROM job_sources"))
    monkeypatch.setattr(linkcheck, "_priorities", lambda db, config, path: ({ids["1"]: 3}, False))
    report = linkcheck.check_links(settings, http=FakeHttp(), now=DAY1)
    assert report["totals"]["checked"] == 2 and report["skipped_ineligible_or_outside_pool"] == 0


# ---- exchange with a cloud runner ------------------------------------------------------------


def export_of(settings, **kw):
    return linkcheck.export_links(settings, now=DAY1, **kw)


def test_export_lists_open_links_in_priority_order_without_private_fields(tmp_path, monkeypatch):
    path, settings = build(tmp_path, count=4)
    ids = dict(rows(path, "SELECT source_id, job_id FROM job_sources"))
    tiers = {ids["1"]: 2, ids["2"]: 0, ids["3"]: 3}  # 4 is outside the pool, 3 is Ineligible
    monkeypatch.setattr(linkcheck, "_priorities", lambda db, config, path: (tiers, True))
    data = export_of(settings)
    assert [i["source_id"] for i in data["items"]] == ["2", "1"]
    assert set(data["items"][0]) == {"source", "board", "source_id", "url"}
    assert data["skipped_ineligible_or_outside_pool"] == 2 and data["version"] == 1
    assert len(export_of(settings, include_ineligible=True)["items"]) == 4


def test_export_ignores_the_twelve_hour_rule(tmp_path):
    path, settings = build(tmp_path, count=1)
    linkcheck.check_links(settings, apply=True, http=FakeHttp(), now=DAY1)
    assert len(export_of(settings)["items"]) == 1


def test_check_export_writes_results_with_only_safe_evidence(tmp_path):
    path, settings = build(tmp_path, count=3)
    data = export_of(settings)
    http = FakeHttp({gone(1): Probe(404), gone(2): Probe(503)})
    summary = linkcheck.check_export(data, tmp_path / "results", http=http, now=DAY1)
    assert summary["checked"] == 3 and summary["removed"] == 1 and summary["unknown"] == 1
    saved = json.loads(Path(summary["file"]).read_text(encoding="utf-8"))
    assert saved["checked_at"] == DAY1.isoformat() and saved["version"] == 1
    removed = next(r for r in saved["results"] if r["verdict"] == "removed")
    assert removed["reason"] == "http_404" and removed["evidence"]["http_status"] == 404
    assert set(removed) == {"source", "board", "source_id", "verdict", "reason", "evidence"}
    limited = linkcheck.check_export(data, tmp_path / "r2", http=FakeHttp(), now=DAY1, limit=1)
    assert limited["checked"] == 1


def test_check_export_refuses_an_unknown_export_format(tmp_path):
    with pytest.raises(ValueError):
        linkcheck.check_export({"version": 99, "items": []}, tmp_path, http=FakeHttp())
    with pytest.raises(ValueError):
        linkcheck.check_export({"version": 1}, tmp_path, http=FakeHttp())


def test_the_bundled_tool_runs_without_the_rest_of_the_project(tmp_path):
    import subprocess
    import sys

    bundle = tmp_path / "tool"
    assert linkcheck.bundle_tool(bundle) == ["__init__.py", "http.py", "linkcheck.py"]
    export = tmp_path / "export.json"
    item = {"source": "greenhouse", "board": "a", "source_id": "1", "url": "http://127.0.0.1/x"}
    export.write_text(json.dumps({"version": 1, "items": [item]}), encoding="utf-8")
    done = subprocess.run(
        [sys.executable, "-I", "-m", "job_scraper.linkcheck", str(export), str(tmp_path / "out")],
        cwd=bundle,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    result = next((tmp_path / "out").glob("*.json"))
    saved = json.loads(result.read_text(encoding="utf-8"))
    assert saved["results"][0]["reason"] == "unusable_url"  # a private address is never fetched


def result_file(folder, when, entries, name=None):
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / (name or f"{when:%Y%m%dT%H%M%SZ}.json")
    file.write_text(
        json.dumps({"version": 1, "checked_at": when.isoformat(), "results": entries}),
        encoding="utf-8",
    )
    return file


def entry(source_id, verdict, reason="x", evidence=None, board="acme", source="greenhouse"):
    return {
        "source": source,
        "board": board,
        "source_id": source_id,
        "verdict": verdict,
        "reason": reason,
        "evidence": evidence or {"http_status": 404, "url": gone(source_id)},
    }


def test_import_dry_run_writes_nothing_and_apply_follows_the_two_day_rule(tmp_path):
    path, settings = build(tmp_path, count=2)
    folder = tmp_path / "results"
    both = [entry("1", "removed", "http_404"), entry("2", "live", "page_available")]
    result_file(folder, DAY1, both)
    dry = linkcheck.import_results(settings, [folder], apply=False, now=DAY1)
    assert dry["mode"] == "dry-run" and dry["recorded"] == 2 and link_events(path) == []
    first = linkcheck.import_results(settings, [folder], apply=True, now=DAY1)
    assert first["summary"] == {"removed": 1, "live": 1} and states(path)["1"] == "active"
    assert len(link_events(path)) == 2 and first["backup"] == ""  # nothing closed: no backup
    result_file(folder, DAY1 + timedelta(days=1), [entry("1", "removed", "http_404")])
    later = DAY1 + timedelta(days=2)
    second = linkcheck.import_results(settings, [folder], apply=True, now=later)
    assert second["summary"]["closed"] == 1 and second["summary"]["already_imported"] == 2
    assert second["backup"]
    assert states(path) == {"1": "closed", "2": "active"}
    sql = "SELECT closure_evidence FROM job_sources WHERE source_id='1'"
    assert json.loads(rows(path, sql)[0][0])["reason"] == "link_check_removed_twice"
    assert rows(path, "SELECT count(*) FROM jobs")[0][0] == 2  # nothing deleted


def test_importing_the_same_files_again_changes_nothing(tmp_path):
    path, settings = build(tmp_path, count=1)
    folder = tmp_path / "results"
    result_file(folder, DAY1, [entry("1", "removed", "http_404")])
    linkcheck.import_results(settings, [folder], apply=True, now=DAY1)
    count = len(link_events(path))
    again = linkcheck.import_results(settings, [folder], apply=True, now=DAY1 + timedelta(hours=1))
    assert again["recorded"] == 0 and again["backup"] == ""
    assert len(link_events(path)) == count


def test_several_days_of_results_imported_at_once_close_in_order(tmp_path):
    path, settings = build(tmp_path, count=1)
    folder = tmp_path / "results"
    for day in range(3):
        result_file(folder, DAY1 + timedelta(days=day), [entry("1", "removed", "http_404")])
    later = DAY1 + timedelta(days=5)
    report = linkcheck.import_results(settings, [folder], apply=True, now=later)
    assert states(path)["1"] == "closed" and report["summary"]["closed"] == 1
    assert report["summary"]["not_open"] == 1  # the third result arrived after it was closed


def test_a_live_result_between_two_removed_ones_prevents_closing_on_import(tmp_path):
    path, settings = build(tmp_path, count=1)
    folder = tmp_path / "results"
    for day, verdict in enumerate(["removed", "live", "removed"]):
        result_file(folder, DAY1 + timedelta(days=day), [entry("1", verdict, "r")])
    linkcheck.import_results(settings, [folder], apply=True, now=DAY1 + timedelta(days=4))
    assert states(path)["1"] == "active"


def test_invalid_and_foreign_results_are_ignored_not_trusted(tmp_path):
    path, settings = build(tmp_path, count=1)
    folder = tmp_path / "results"
    odd = entry("1", "removed", "x", evidence={"http_status": 404, "evil": "<script>", "url": "u"})
    result_file(
        folder,
        DAY1,
        [
            entry("1", "live", "ok"),
            entry("1", "deleted", "bogus verdict"),
            entry("9", "removed", "http_404"),  # not a job in this database
            {"source": 5, "board": "a", "source_id": "1", "verdict": "removed", "reason": "x"},
            "not a dict",
            odd,
        ],
    )
    (folder / "broken.json").write_text("{not json", encoding="utf-8")
    naive = json.dumps({"version": 1, "checked_at": "2026-11-24T10:00:00", "results": []})
    (folder / "naive.json").write_text(naive, encoding="utf-8")
    report = linkcheck.import_results(settings, [folder], apply=True, now=DAY1)
    assert report["invalid_files_or_entries"] == 5  # 3 bad entries, 2 bad files
    assert report["summary"]["unknown_job"] == 1
    stored = [json.loads(e[3]) for e in link_events(path)]
    assert all("evil" not in s for s in stored)  # only whitelisted evidence is kept
    assert "Link import (apply)" in linkcheck.render_import(report)


def test_the_whole_exchange_round_trip(tmp_path):
    path, settings = build(tmp_path, count=2)
    data = export_of(settings)
    http = FakeHttp({gone(1): Probe(410)})
    out = tmp_path / "cloud" / "results"
    linkcheck.check_export(data, out, http=http, now=DAY1)
    linkcheck.check_export(data, out, http=http, now=DAY1 + timedelta(days=1))
    later = DAY1 + timedelta(days=1, hours=1)
    report = linkcheck.import_results(settings, [out], apply=True, now=later)
    assert states(path) == {"1": "closed", "2": "active"}
    assert report["summary"]["closed"] == 1


def test_cli_export_and_import(tmp_path, capsys):
    from job_scraper import cli

    path, settings = build(tmp_path, count=2)
    config = tmp_path / "config.toml"
    config.write_text(
        f'database = "{path.as_posix()}"\n[[sources]]\nsource="arbeitnow"', encoding="utf-8"
    )
    out = tmp_path / "x" / "export.json"
    tool = tmp_path / "t"
    base = ["link-export", "--config", str(config), "--out", str(out), "--bundle-dir", str(tool)]
    assert cli.main(base) == 0
    assert "Exported" in capsys.readouterr().out
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data["items"]) == 2 and (tool / "job_scraper" / "http.py").exists()
    folder = tmp_path / "results"
    result_file(folder, DAY1, [entry("1", "live", "page_available")])
    assert cli.main(["link-import", "--config", str(config), str(folder)]) == 0
    assert "Link import (dry-run)" in capsys.readouterr().out


# ---- unknown-only re-check and the backup policy ----------------------------------------------


def test_only_unknown_rechecks_the_sources_whose_last_check_was_unknown(tmp_path):
    path, settings = build(tmp_path, count=3)
    blocked = FakeHttp({gone(1): Probe(403), gone(2): Probe(403)})
    linkcheck.check_links(settings, apply=True, http=blocked, now=DAY1)
    later = DAY1 + timedelta(days=1)
    http = FakeHttp({gone(1): Probe(410)})  # the laptop can see what the cloud could not
    report = linkcheck.check_links(settings, apply=True, http=http, now=later, only_unknown=True)
    # sources 1 and 2 were unknown; source 3 was live and is left alone
    assert report["totals"]["checked"] == 2
    assert report["totals"]["removed_first_check"] == 1 and report["totals"]["live"] == 1
    assert states(path) == {"1": "active", "2": "active", "3": "active"}


def test_backups_are_kept_to_the_newest_few(tmp_path):
    path, settings = build(tmp_path, count=1)
    folder = path.parent / "backups"
    folder.mkdir(exist_ok=True)
    for day in range(1, 6):
        (folder / f"jobs-pre-linkcheck-2026010{day}T000000000000Z.sqlite3").write_text("old")
    http = FakeHttp({gone(1): Probe(404)})
    linkcheck.check_links(settings, apply=True, http=http, now=DAY1)
    linkcheck.check_links(settings, apply=True, http=http, now=DAY1 + timedelta(days=1))
    kept = sorted(folder.glob("jobs-pre-linkcheck-*.sqlite3"))
    assert len(kept) == linkcheck.BACKUPS_KEPT
    assert kept[0].name.startswith("jobs-pre-linkcheck-20260104")
