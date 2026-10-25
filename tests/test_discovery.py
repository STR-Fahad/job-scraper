import socket

import httpx
import pytest

from job_scraper.config import SourceConfig
from job_scraper.discovery import discover, parse_board
from job_scraper.http import CollectionError, HttpClient
from job_scraper.registry import approve_board, effective_sources, set_enabled
from job_scraper.storage import Store


@pytest.mark.parametrize(
    "url,source,board,region",
    [
        ("https://job-boards.greenhouse.io/acme/jobs/123", "greenhouse", "acme", "global"),
        ("https://boards.greenhouse.io/embed/job_board?for=acme", "greenhouse", "acme", "global"),
        ("https://jobs.ashbyhq.com/acme/uuid", "ashby", "acme", "global"),
        ("https://jobs.lever.co/acme/uuid/apply", "lever", "acme", "global"),
        ("https://jobs.eu.lever.co/acme", "lever", "acme", "eu"),
    ],
)
def test_parse_only_real_link_identifiers(url, source, board, region):
    config = parse_board(url, "Acme")
    assert (config.source, config.board, config.region) == (source, board, region)


@pytest.mark.parametrize(
    "url",
    [
        "https://jobs.lever.co.evil.test/acme",
        "http://jobs.lever.co/acme",
        "https://user:secret@jobs.lever.co/acme",
        "https://jobs.ashbyhq.com/",
        "https://jobs.lever.co/acme%2Fprivate",
    ],
)
def test_reject_spoofed_and_invalid_links(url):
    assert parse_board(url, "Acme") is None


def resolver(*args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


def test_careers_discovery_deduplicates_and_preserves_manual_config():
    calls = []

    def response(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        if request.url.host == "company.example":
            return httpx.Response(
                200,
                text='<a href="https://jobs.ashbyhq.com/acme">Jobs</a><a href="https://jobs.ashbyhq.com/acme/one">Job</a>',
            )
        return httpx.Response(200, json={"jobs": [{"jobUrl": "https://jobs.ashbyhq.com/acme/one"}]})

    with (
        Store(":memory:") as store,
        httpx.Client(transport=httpx.MockTransport(response)) as client,
    ):
        http = HttpClient(client, sleep=lambda _: None)
        result = discover(
            store,
            http,
            "https://company.example/careers",
            "Acme",
            allow_careers_fetch=True,
            approve_verified=True,
            resolver=resolver,
        )
        assert len(result) == 1 and result[0]["verified"]
        assert len(calls) == 3
        row = store.db.execute("SELECT * FROM company_boards").fetchone()
        assert row["discovery_url"] == "https://company.example/careers" and row["verified_at"]
        assert not effective_sources(store, [])[0].enabled
        set_enabled(store, [], "ashby", "acme", True)
        assert effective_sources(store, [])[0].enabled
        manual = SourceConfig(source="ashby", board="acme", company="Manual Name")
        assert effective_sources(store, [manual])[0].company == "Manual Name"
        discover(store, http, "https://jobs.ashbyhq.com/acme", "Acme", approve_verified=True)
        assert store.db.execute("SELECT count(*) FROM company_boards").fetchone()[0] == 1


def test_uncertain_empty_board_requires_manual_approval():
    with (
        Store(":memory:") as store,
        httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"jobs": []}))
        ) as client,
    ):
        result = discover(
            store,
            HttpClient(client),
            "https://jobs.ashbyhq.com/acme",
            "Acme",
            approve_verified=True,
        )
        assert result[0]["approval_required"]
        assert not effective_sources(store, [])
        approve_board(store, "ashby", "acme")
        set_enabled(store, [], "ashby", "acme", True)
        assert effective_sources(store, [])[0].enabled


def test_failed_verification_is_pending_not_collectible():
    with (
        Store(":memory:") as store,
        httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403))) as client,
    ):
        result = discover(
            store, HttpClient(client), "https://jobs.lever.co/acme", "Acme", approve_verified=True
        )
        assert not result[0]["verified"]
        with pytest.raises(ValueError, match="verification"):
            approve_board(store, "lever", "acme")
        assert effective_sources(store, []) == []


def test_robots_denial_prevents_careers_request():
    calls = []

    def response(request):
        calls.append(str(request.url))
        return httpx.Response(200, text="User-agent: *\nDisallow: /")

    with (
        Store(":memory:") as store,
        httpx.Client(transport=httpx.MockTransport(response)) as client,
    ):
        with pytest.raises(CollectionError, match="robots"):
            discover(
                store,
                HttpClient(client),
                "https://company.example/careers",
                "Acme",
                allow_careers_fetch=True,
                resolver=resolver,
            )
        assert len(calls) == 1


def test_discovery_requires_permission_and_rejects_private_addresses():
    with Store(":memory:") as store:
        with pytest.raises(ValueError, match="confirm"):
            discover(store, None, "https://company.example/careers", "Acme")

        def local(*a, **kw):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

        with pytest.raises(ValueError, match="Private"):
            discover(
                store,
                None,
                "https://company.example/careers",
                "Acme",
                allow_careers_fetch=True,
                resolver=local,
            )
