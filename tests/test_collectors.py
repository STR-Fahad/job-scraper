import httpx
import pytest

from job_scraper.http import CollectionError


@pytest.mark.parametrize(
    "source",
    [
        "himalayas",
        "greenhouse",
        "ashby",
        "lever",
        "remotive",
        "arbeitnow",
        "jobicy",
        "remoteok",
        "workingnomads",
        "weworkremotely",
    ],
)
def test_normalization(source, rows, make_collector):
    collector, _, _ = make_collector(source)
    job = collector.normalize(rows[source])
    assert job.company == "Acme"
    assert job.title == "Engineer"
    assert "Full description" in job.description
    assert job.raw == rows[source]
    assert job.location
    assert str(job.source_url).startswith("https://")
    if source in {"greenhouse", "himalayas", "lever"}:
        assert job.posted_at is None
    if source == "himalayas":
        assert job.salary.period == "hourly"
        assert job.restrictions["timezoneRestrictions"] == ["UTC+5"]
        assert job.updated_at.tzinfo is not None
    if source == "lever":
        assert "Python" in job.description and "Closing section" in job.description
        assert job.location == ["London", "Paris"]
    if source == "ashby":
        assert job.source_id == rows[source]["jobUrl"]
        assert job.salary.text == "$50K-$60K"
    if source == "remotive":
        assert job.application_url is None
        assert job.posted_at.utcoffset().total_seconds() == 0


def test_himalayas_cursor_not_total_count(rows, make_collector):
    collector, calls, stats = make_collector(
        "himalayas",
        [
            {"jobs": [rows["himalayas"]], "nextCursor": "opaque+/=", "totalCount": 0},
            {"jobs": [rows["himalayas"]], "totalCount": 99999},
        ],
    )
    assert len(list(collector.collect())) == 2
    assert "cursor" not in calls[0].url.params
    assert calls[1].url.params["cursor"] == "opaque+/="
    assert stats.pages == 2 and not stats.truncated


def test_himalayas_limit_and_cycle(rows, make_collector):
    payload = {"jobs": [rows["himalayas"]], "nextCursor": "same"}
    collector, calls, stats = make_collector("himalayas", [payload], max_pages=1)
    assert len(list(collector.collect())) == 1
    assert stats.truncated and len(calls) == 1
    collector, _, _ = make_collector("himalayas", [payload, payload])
    with pytest.raises(CollectionError, match="repeated"):
        list(collector.collect())


def test_himalayas_key_is_header_only(rows, make_collector, monkeypatch):
    monkeypatch.setenv("HIMALAYAS_API_KEY", "test-secret")
    collector, calls, _ = make_collector("himalayas", [{"jobs": [rows["himalayas"]]}])
    list(collector.collect())
    assert calls[0].headers["authorization"] == "Bearer test-secret"
    assert "test-secret" not in str(calls[0].url)


def test_lever_pagination_and_region(rows, make_collector):
    row = rows["lever"]
    second = {**row, "id": "two"}
    collector, calls, stats = make_collector(
        "lever", [[row], [second], []], page_size=1, region="eu"
    )
    assert len(list(collector.collect())) == 2
    assert [r.url.params["skip"] for r in calls] == ["0", "1", "2"]
    assert calls[0].url.host == "api.eu.lever.co"
    assert stats.pages == 3 and not stats.truncated


def test_lever_repeated_page(rows, make_collector):
    collector, _, _ = make_collector("lever", [[rows["lever"]], [rows["lever"]]], page_size=1)
    with pytest.raises(CollectionError, match="repeated"):
        list(collector.collect())


def test_arbeitnow_next_links(rows, make_collector):
    collector, calls, stats = make_collector(
        "arbeitnow",
        [
            {
                "data": [rows["arbeitnow"]],
                "links": {"next": "?page=2"},
                "meta": {"terms": "credit"},
            },
            {"data": [], "links": {"next": None}},
        ],
    )
    jobs = list(collector.collect())
    assert len(jobs) == 1 and stats.pages == 2
    assert calls[1].url.params["page"] == "2"
    assert jobs[0].response_metadata["meta"]["terms"] == "credit"


@pytest.mark.parametrize(
    "next_link",
    [
        "https://evil.example/",
        "http://www.arbeitnow.com/api/job-board-api",
        "https://www.arbeitnow.com/private",
    ],
)
def test_arbeitnow_refuses_untrusted_next(next_link, make_collector):
    collector, calls, _ = make_collector("arbeitnow", [{"data": [], "links": {"next": next_link}}])
    with pytest.raises(CollectionError, match="outside"):
        list(collector.collect())
    assert len(calls) == 1


def test_arbeitnow_cycle(make_collector):
    url = "https://www.arbeitnow.com/api/job-board-api"
    collector, _, _ = make_collector("arbeitnow", [{"data": [], "links": {"next": url}}])
    with pytest.raises(CollectionError, match="repeated"):
        list(collector.collect())


@pytest.mark.parametrize("source", ["ashby", "greenhouse", "remotive"])
def test_nonpaginated_sources(source, rows, make_collector):
    collector, calls, stats = make_collector(source, [{"jobs": [rows[source]]}])
    assert len(list(collector.collect())) == 1
    assert len(calls) == 1 and not stats.truncated
    assert "page" not in calls[0].url.params
    if source == "ashby":
        assert calls[0].url.params["includeCompensation"] == "true"
    if source == "greenhouse":
        assert calls[0].url.params["content"] == "true"
    if source == "remotive":
        assert "limit" not in calls[0].url.params


def test_greenhouse_detail_and_fallback(rows, make_collector):
    raw = rows["greenhouse"]
    detail = {
        **raw,
        "pay_input_ranges": [{"min_cents": 10000, "max_cents": 20000, "currency_type": "USD"}],
    }
    collector, calls, stats = make_collector(
        "greenhouse", [{"jobs": [raw]}, detail], fetch_details=True
    )
    job = list(collector.collect())[0]
    assert job.salary.details["pay_input_ranges"]
    assert calls[1].url.params["pay_transparency"] == "true" and stats.errors == 0
    collector, _, stats = make_collector(
        "greenhouse", [{"jobs": [raw]}, httpx.Response(404)], fetch_details=True
    )
    assert list(collector.collect())[0].description
    assert stats.errors == 1


def test_invalid_record_does_not_discard_neighbors(rows, make_collector):
    collector, _, stats = make_collector("remotive", [{"jobs": [{}, rows["remotive"], None]}])
    assert len(list(collector.collect())) == 1
    assert stats.invalid == 2 and stats.fetched == 3


@pytest.mark.parametrize("payload", [{}, {"jobs": None}, {"jobs": "not an array"}, []])
def test_bad_envelope_is_error(payload, make_collector):
    collector, _, _ = make_collector("remotive", [payload])
    with pytest.raises(CollectionError):
        list(collector.collect())


@pytest.mark.parametrize("source", ["lever", "arbeitnow"])
def test_other_paginated_sources_respect_cap(source, rows, make_collector):
    payload = (
        [rows[source]]
        if source == "lever"
        else {"data": [rows[source]], "links": {"next": "?page=2"}}
    )
    collector, calls, stats = make_collector(source, [payload], max_pages=1, page_size=1)
    assert len(list(collector.collect())) == 1
    assert len(calls) == 1 and stats.truncated


def test_optional_nulls_and_html_fallback(rows, make_collector):
    collector, _, _ = make_collector("greenhouse")
    assert collector.normalize({**rows["greenhouse"], "location": None}).location == []
    collector, _, _ = make_collector("ashby")
    result = collector.normalize(
        {**rows["ashby"], "descriptionPlain": "", "secondaryLocations": [None, {}]}
    )
    assert result.description == "Full description"
    assert result.location == ["London"]


def test_greenhouse_stops_details_after_denial(rows, make_collector):
    collector, calls, stats = make_collector(
        "greenhouse",
        [
            {"jobs": [rows["greenhouse"], {**rows["greenhouse"], "id": 2}]},
            httpx.Response(403),
        ],
        fetch_details=True,
    )
    assert len(list(collector.collect())) == 2
    assert len(calls) == 2 and stats.errors == 1


def test_missing_source_identifier_is_invalid(rows, make_collector):
    collector, _, stats = make_collector("remotive", [{"jobs": [{**rows["remotive"], "id": None}]}])
    assert list(collector.collect()) == []
    assert stats.invalid == 1


def test_jobicy_geo_is_split_and_level_kept(rows, make_collector):
    collector, _, _ = make_collector("jobicy")
    job = collector.normalize(rows["jobicy"])
    assert job.location == ["Poland", "Portugal"]
    assert job.restrictions == {"jobLevel": "Junior"}
    assert job.attribution == "Jobicy" and str(job.attribution_url) == "https://jobicy.com/"
    assert job.workplace_type == "remote"


def test_remoteok_skips_the_legal_notice_and_credits_the_source(rows, make_collector):
    legal = {"last_updated": 1, "legal": "API Terms of Service: link back"}
    collector, _, stats = make_collector("remoteok", [[legal, rows["remoteok"]]])
    jobs = list(collector.collect())
    assert [j.source_id for j in jobs] == ["1"] and stats.fetched == 1
    assert jobs[0].attribution == "Remote OK"


def test_workingnomads_source_id_is_the_numeric_job_id(rows, make_collector):
    collector, _, _ = make_collector("workingnomads")
    assert collector.normalize(rows["workingnomads"]).source_id == "2027058"
    rows["workingnomads"]["url"] = "https://www.workingnomads.com/job/go/abc"
    assert collector.normalize(rows["workingnomads"]).source_id.endswith("/abc")


RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>t</title>
<item><title>Acme: Python Engineer</title><region>Latin America Only</region><country></country>
<state></state><skills>Python</skills><category>Back-End</category><type>Full-Time</type>
<description>&lt;p&gt;Full description&lt;/p&gt;</description>
<link>https://weworkremotely.com/remote-jobs/acme-python</link>
<guid>https://weworkremotely.com/remote-jobs/acme-python</guid>
<pubDate>Mon, 16 Nov 2026 11:56:02 +0000</pubDate></item>
<item><title>No company separator</title><link>https://weworkremotely.com/x</link></item>
</channel></rss>"""


def test_weworkremotely_parses_rss_and_counts_bad_items(make_collector):
    def handler(request):
        return httpx.Response(200, text=RSS)

    collector, _, stats = make_collector("weworkremotely", handler=handler)
    jobs = list(collector.collect())
    # one valid item per feed; the item without "Company: Title" is counted invalid
    assert {j.company for j in jobs} == {"Acme"}
    assert jobs[0].location == ["Latin America Only"] and jobs[0].title == "Python Engineer"
    assert stats.invalid >= 1


def test_weworkremotely_refuses_dtd_feeds(make_collector):
    bad = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "b">]><rss><channel/></rss>'
    collector, _, _ = make_collector(
        "weworkremotely", handler=lambda request: httpx.Response(200, text=bad)
    )
    with pytest.raises(CollectionError, match="DTD"):
        list(collector.collect())


@pytest.mark.parametrize("source", ["jobicy", "remoteok", "workingnomads", "weworkremotely"])
def test_new_aggregates_are_paced_politely(source):
    from job_scraper.config import SourceConfig

    config = SourceConfig(source=source, interval_seconds=0.1, cooldown_seconds=0)
    assert config.interval_seconds >= 5 and config.cooldown_seconds >= 21600
