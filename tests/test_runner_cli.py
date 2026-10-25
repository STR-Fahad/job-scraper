import json

import httpx
import pytest
from pydantic import ValidationError

from job_scraper import cli
from job_scraper.config import Settings, SourceConfig, load_settings
from job_scraper.models import Statistics
from job_scraper.runner import collect
from job_scraper.storage import Store


def test_run_all_continues_after_source_failure(tmp_path, rows):
    settings = Settings(
        database=tmp_path / "jobs.sqlite3",
        retries=0,
        sources=[
            SourceConfig(source="greenhouse", board="acme", company="Acme"),
            SourceConfig(source="arbeitnow"),
        ],
    )

    def handler(request):
        if "greenhouse" in request.url.host:
            return httpx.Response(403)
        return httpx.Response(200, json={"data": [rows["arbeitnow"]], "links": {"next": None}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        stats = collect(settings, client=client, sleep=lambda _: None)
    assert stats[0].errors == 1
    assert stats[1].inserted == 1
    with Store(settings.database) as store:
        assert store.db.execute("SELECT count(*) FROM collection_runs").fetchone()[0] == 2


def test_partial_pages_survive_later_error(tmp_path, rows):
    settings = Settings(
        database=tmp_path / "jobs.sqlite3", retries=0, sources=[SourceConfig(source="arbeitnow")]
    )
    responses = iter(
        [
            httpx.Response(200, json={"data": [rows["arbeitnow"]], "links": {"next": "?page=2"}}),
            httpx.Response(503),
        ]
    )
    with httpx.Client(transport=httpx.MockTransport(lambda r: next(responses))) as client:
        stats = collect(settings, client=client, sleep=lambda _: None)
    assert stats[0].inserted == 1 and stats[0].errors == 1
    with Store(settings.database) as store:
        assert store.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1


def test_runner_cooldown_skips_network(tmp_path, rows):
    settings = Settings(
        database=tmp_path / "jobs.sqlite3", sources=[SourceConfig(source="remotive")]
    )
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"jobs": [rows["remotive"]]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert collect(settings, client=client)[0].inserted == 1
        assert collect(settings, client=client)[0].skipped
    assert len(calls) == 1


def test_source_and_board_filter(tmp_path):
    settings = Settings(
        database=tmp_path / "jobs.sqlite3",
        sources=[
            SourceConfig(source="ashby", board="one", company="One"),
            SourceConfig(source="ashby", board="two", company="Two"),
            SourceConfig(source="arbeitnow"),
        ],
    )
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"jobs": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        stats = collect(settings, source="ashby", board="two", client=client)
    assert len(stats) == len(calls) == 1
    assert calls[0].url.path.endswith("/two")


def test_configuration_validation_and_defaults(tmp_path):
    with pytest.raises(ValidationError):
        SourceConfig(source="ashby")
    with pytest.raises(ValidationError):
        SourceConfig(source="lever", board="../private", company="Acme")
    with pytest.raises(ValidationError):
        Settings(sources=[SourceConfig(source="remotive"), SourceConfig(source="remotive")])
    assert SourceConfig(source="remotive", interval_seconds=1).interval_seconds == 31
    assert SourceConfig(source="himalayas", page_size=100).page_size == 20
    config = tmp_path / "config.toml"
    config.write_text('[[sources]]\nsource="arbeitnow"', encoding="utf-8")
    assert load_settings(config).database == tmp_path / "data/jobs.sqlite3"


def test_empty_selection_is_explicit(tmp_path):
    settings = Settings(database=tmp_path / "unused.sqlite3", sources=[])
    with pytest.raises(ValueError, match="No enabled"):
        collect(settings)
    assert not settings.database.exists()


def test_cli_json_output_and_exit_codes(tmp_path, monkeypatch, capsys):
    path = tmp_path / "config.toml"
    path.write_text('[[sources]]\nsource="arbeitnow"', encoding="utf-8")
    monkeypatch.setattr(
        cli, "collect", lambda *a, **kw: [Statistics(source="arbeitnow", inserted=2)]
    )
    args = ["collect", "--all", "--config", str(path), "--json"]
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)[0]["inserted"] == 2
    monkeypatch.setattr(cli, "collect", lambda *a, **kw: [Statistics(source="arbeitnow", errors=1)])
    assert cli.main(args) == 1
    assert cli.main(["collect", "--all", "--config", str(tmp_path / "missing.toml")]) == 2


def test_cli_rejects_invalid_page_limit():
    with pytest.raises(SystemExit) as exc:
        cli.main(["collect", "--all", "--max-pages", "0"])
    assert exc.value.code == 2
