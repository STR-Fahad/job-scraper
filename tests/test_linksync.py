"""Start-up link sync: pull the cloud results, import them, re-check unknowns, publish the export."""

import json
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from test_linkcheck import DAY1, build, entry, link_events, result_file

from job_scraper import linkcheck, linksync


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(linkcheck, "_priorities", lambda db, config, path: ({}, False))
    for name in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(name, "tester")
    for name in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(name, "tester@example.com")


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_repo(tmp_path):
    """A links repo cloned from a local bare 'origin', plus a second clone that plays the cloud."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    cloud = tmp_path / "cloud"
    git(tmp_path, "clone", str(origin), str(cloud))
    (cloud / "export.json").write_text("{}", encoding="utf-8")
    git(cloud, "add", ".")
    git(cloud, "commit", "-m", "start")
    git(cloud, "push", "origin", "HEAD:main")
    repo = tmp_path / "project" / "work" / "links-repo"
    repo.parent.mkdir(parents=True)
    git(tmp_path, "clone", str(origin), str(repo))
    return repo, cloud


def cloud_pushes_results(cloud, when, entries):
    result_file(cloud / "results", when, entries)
    git(cloud, "add", ".")
    git(cloud, "commit", "-m", "results")
    git(cloud, "push", "origin", "HEAD:main")


def test_without_a_links_repo_the_sync_is_skipped(tmp_path):
    path, settings = build(tmp_path)
    report = linksync.sync_links(settings)
    assert "skipped" in report and link_events(path) == []
    assert "skipped" in linksync.summary(report)


def test_the_repo_is_found_next_to_the_project_data_folder(tmp_path):
    (tmp_path / "work" / "links-repo" / ".git").mkdir(parents=True)
    assert linksync.links_repo(tmp_path / "data" / "jobs.sqlite3") == (
        tmp_path / "work" / "links-repo"
    )
    assert linksync.links_repo(tmp_path / "elsewhere" / "deeper" / "jobs.sqlite3") is None


def test_the_sync_pulls_imports_rechecks_unknowns_and_publishes(tmp_path, monkeypatch):
    repo, cloud = make_repo(tmp_path)
    path = repo.parent.parent / "data" / "jobs.sqlite3"
    path.parent.mkdir()
    _, settings = build(repo.parent.parent, count=2)
    cloud_pushes_results(
        cloud,
        DAY1,
        [entry("1", "live", "page_available"), entry("2", "unknown", "http_403")],
    )
    seen = {}

    def recheck(settings_, **kwargs):
        seen.update(kwargs)
        return {"totals": {"checked": 1, "live": 0, "removed_first_check": 1, "closed": 0}}

    monkeypatch.setattr(linksync, "check_links", recheck)
    report = linksync.sync_links(settings, repo=repo, now=DAY1)
    assert report["pull"] == "pulled"
    assert report["import"]["recorded"] == 2 and report["import"]["live"] == 1
    assert seen["only_unknown"] is True and seen["apply"] is True
    assert report["recheck_unknown"]["removed_first_check"] == 1
    assert report["publish"].startswith("exported")
    assert len(link_events(path)) == 2
    export = json.loads((repo / "export.json").read_text(encoding="utf-8"))
    assert len(export["items"]) == 2
    # the refreshed export reached the shared origin
    git(cloud, "pull", "--ff-only", "origin", "main")
    assert len(json.loads((cloud / "export.json").read_text(encoding="utf-8"))["items"]) == 2


def test_a_second_sync_with_nothing_new_changes_nothing(tmp_path, monkeypatch):
    repo, cloud = make_repo(tmp_path)
    _, settings = build(repo.parent.parent, count=1)
    monkeypatch.setattr(linksync, "check_links", lambda *a, **k: {"totals": {}})
    cloud_pushes_results(cloud, DAY1, [entry("1", "live", "page_available")])
    linksync.sync_links(settings, repo=repo, now=DAY1)
    again = linksync.sync_links(settings, repo=repo, now=DAY1 + timedelta(hours=1))
    assert again["pull"] == "up to date"
    assert again["import"]["recorded"] == 0
    assert again["publish"] == "export unchanged"


def test_one_failing_step_does_not_stop_the_others(tmp_path, monkeypatch):
    repo, cloud = make_repo(tmp_path)
    _, settings = build(repo.parent.parent, count=1)

    def broken(*args, **kwargs):
        raise OSError("network down")

    monkeypatch.setattr(linksync, "check_links", broken)
    monkeypatch.setattr(linksync, "_pull", broken)
    report = linksync.sync_links(settings, repo=repo, now=datetime.now(UTC))
    assert report["pull"].startswith("failed") and report["recheck_unknown"].startswith("failed")
    assert report["publish"].startswith("exported")  # later steps still ran
    assert "Problems" in linksync.summary(report)


def test_run_and_log_writes_one_line_and_reports(tmp_path):
    path, settings = build(tmp_path)
    said = []
    report = linksync.run_and_log(settings, say=said.append)
    assert "skipped" in report and said and "skipped" in said[0]
    lines = (path.parent / linksync.LOG_NAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and "skipped" in json.loads(lines[0])


def test_an_export_committed_earlier_but_not_pushed_is_pushed_next_time(tmp_path, monkeypatch):
    repo, cloud = make_repo(tmp_path)
    _, settings = build(repo.parent.parent, count=1)
    monkeypatch.setattr(linksync, "check_links", lambda *a, **k: {"totals": {}})
    real_push = linksync._git

    def failing_push(repo_, *args):
        if args[:1] == ("push",):
            return subprocess.CompletedProcess(args, 1, "", "denied")
        return real_push(repo_, *args)

    monkeypatch.setattr(linksync, "_git", failing_push)
    first = linksync.sync_links(settings, repo=repo, now=DAY1)
    assert first["publish"].startswith("failed")
    monkeypatch.setattr(linksync, "_git", real_push)
    second = linksync.sync_links(settings, repo=repo, now=DAY1 + timedelta(days=1))
    assert second["publish"] == "pushed an earlier export"
    git(cloud, "pull", "--ff-only", "origin", "main")
    assert len(json.loads((cloud / "export.json").read_text(encoding="utf-8"))["items"]) == 1
