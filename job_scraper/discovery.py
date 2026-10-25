"""Bounded discovery from supplied careers links; no guessed employer names."""

import ipaddress
import socket
from urllib.parse import parse_qs, unquote, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

from .config import SourceConfig
from .http import CollectionError
from .registry import save_candidate

USER_AGENT = "personal-job-scraper"


def parse_board(url, company):
    p = urlsplit(url)
    if p.scheme != "https" or p.username or p.password or p.port not in (None, 443):
        return None
    hosts = {
        "boards.greenhouse.io": ("greenhouse", "global"),
        "job-boards.greenhouse.io": ("greenhouse", "global"),
        "jobs.lever.co": ("lever", "global"),
        "jobs.eu.lever.co": ("lever", "eu"),
        "jobs.ashbyhq.com": ("ashby", "global"),
    }
    if p.hostname not in hosts:
        return None
    source, region = hosts[p.hostname]
    parts = p.path.strip("/").split("/")
    board = (
        parse_qs(p.query).get("for", [""])[0]
        if source == "greenhouse" and parts[0] == "embed"
        else parts[0]
    )
    if not board:
        return None
    try:
        return SourceConfig(
            source=source, board=unquote(board), region=region, company=company, enabled=False
        )
    except ValueError:
        return None


def public_url(url, resolver=socket.getaddrinfo):
    p = urlsplit(url)
    if (
        p.scheme != "https"
        or not p.hostname
        or p.username
        or p.password
        or p.port not in (None, 443)
    ):
        raise ValueError("Discovery requires a public HTTPS URL on port 443")
    addresses = resolver(p.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("Private, loopback, and reserved addresses are not allowed")
    return p


def verify_board(http, config):
    if config.source == "greenhouse":
        url = f"https://boards-api.greenhouse.io/v1/boards/{config.board}/jobs"
        payload = http.get(url)
    elif config.source == "ashby":
        url = f"https://api.ashbyhq.com/posting-api/job-board/{config.board}"
        payload = http.get(url)
    else:
        host = "api.eu.lever.co" if config.region == "eu" else "api.lever.co"
        url = f"https://{host}/v0/postings/{config.board}"
        payload = http.get(url, params={"mode": "json", "limit": 1})
    rows = (
        payload
        if config.source == "lever"
        else payload.get("jobs")
        if isinstance(payload, dict)
        else None
    )
    if not isinstance(rows, list):
        raise CollectionError("Board did not return the documented postings schema")
    id_field = {"greenhouse": "id", "ashby": "jobUrl", "lever": "id"}[config.source]
    if any(not isinstance(r, dict) or not r.get(id_field) for r in rows):
        raise CollectionError("Board returned invalid job identifiers")
    return {"endpoint": url, "sample_count": len(rows), "empty_board": not rows}


def discover(
    store,
    http,
    url,
    company,
    *,
    allow_careers_fetch=False,
    approve_verified=False,
    max_candidates=20,
    resolver=socket.getaddrinfo,
):
    """Approved sources: direct public ATS URLs, or explicitly permitted careers pages.

    Only the supplied page and its robots.txt are read. Redirects/access denials
    stop discovery. No search-engine scraping or recursive website crawling.
    """
    candidate = parse_board(url, company)
    candidates = [(url, candidate)] if candidate else []
    if not candidate:
        if not allow_careers_fetch:
            raise ValueError(
                "For a company page, confirm permitted access with --allow-careers-fetch"
            )
        p = public_url(url, resolver)
        robots_url = f"https://{p.netloc}/robots.txt"
        robots_text = http.get(robots_url, as_text=True, headers={"Accept": "text/plain"})
        robots = RobotFileParser()
        robots.parse(robots_text.splitlines())
        if not robots.can_fetch(USER_AGENT, url):
            raise CollectionError("Careers page disallowed by robots.txt")
        interval = max(2, robots.crawl_delay(USER_AGENT) or 0)
        page = http.get(url, as_text=True, interval=interval, headers={"Accept": "text/html"})
        soup = BeautifulSoup(page, "html.parser")
        for tag in soup.select("a[href], iframe[src]"):
            link = urljoin(url, tag.get("href") or tag.get("src"))
            parsed = parse_board(link, company)
            if parsed:
                candidates.append((link, parsed))
    results, seen = [], set()
    for board_url, config in candidates:
        identity = (config.source, config.scope)
        if identity in seen:
            continue
        seen.add(identity)
        if len(results) >= max_candidates:
            break
        try:
            evidence = verify_board(http, config)
            verified = True
        except CollectionError as exc:
            evidence, verified = {"error": str(exc)}, False
        # An empty board's ownership is uncertain: retain for manual approval.
        results.append(
            save_candidate(
                store,
                config,
                url,
                board_url,
                verified,
                evidence,
                approve=approve_verified and not evidence.get("empty_board", True),
            )
        )
    return results
