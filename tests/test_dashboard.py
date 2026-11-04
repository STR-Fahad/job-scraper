"""Module 4 dashboard: read model, safety rules, tracking actions and review append."""

import io
import json
import re
import socket
from unittest.mock import MagicMock
from urllib.parse import urlencode

import pytest
from test_requirement_inventory import JOB22, _classifications
from test_storage import job

from job_scraper import tracking
from job_scraper.candidate import CandidateProfile, ProfileEntry, profile_hash, profile_json
from job_scraper.config import MatchingConfig
from job_scraper.dashboard import data, render
from job_scraper.dashboard.app import Dashboard
from job_scraper.matching import analyze, ensure_schema
from job_scraper.processing import process_jobs
from job_scraper.requirements import extract_requirements
from job_scraper.storage import Store

PORT = 8765
DESCRIPTION = JOB22.read_text(encoding="utf-8")


def call(app, method, path, form=None, *, host=f"127.0.0.1:{PORT}", origin=None):
    path, _, query = path.partition("?")
    body = urlencode(form or {}).encode()
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "HTTP_HOST": host,
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
    }
    if origin:
        environ["HTTP_ORIGIN"] = origin
    captured = {}

    def start(status, headers):
        captured["status"], captured["headers"] = status, dict(headers)

    text = b"".join(app(environ, start)).decode()
    return int(captured["status"].split()[0]), captured["headers"], text


def post(app, path, form, **kwargs):
    return call(app, "POST", path, {"csrf": app.csrf, **form}, **kwargs)


def seed(store, count=0):
    """Job 1 has a current mocked assessment; others vary in relevance and eligibility."""
    store.upsert(
        job(
            source="remotive",
            source_id="assessed",
            source_url="https://example.test/jobs/assessed",
            company="Askable",
            title="Software Engineer, AI Coding Tasks",
            description=DESCRIPTION,
            workplace_type="remote",
        )
    )
    store.upsert(
        job(
            source="remotive",
            source_id="plain",
            source_url="https://example.test/jobs/plain",
            company="Plainco",
            title="Backend Python Engineer",
            description="Build Python APIs. Work from anywhere. Experience with Django helps.",
            workplace_type="remote",
        )
    )
    store.upsert(
        job(
            source="arbeitnow",
            source_id="senior",
            source_url="https://example.test/jobs/senior",
            company="Bigco",
            title="Senior Backend Engineer",
            description="8+ years of software engineering experience required.",
            workplace_type="remote",
        )
    )
    store.upsert(
        job(
            source="arbeitnow",
            source_id="sales",
            source_url="https://example.test/jobs/sales",
            company="Salesco",
            title="Sales Manager",
            description="Sell things to customers.",
            workplace_type="onsite",
        )
    )
    for n in range(count):
        store.upsert(
            job(
                source="remotive",
                source_id=f"bulk{n}",
                source_url=f"https://example.test/jobs/bulk{n}",
                company=f"Bulk {n:02d}",
                title=f"Python Developer {n:02d}",
                description="Python developer role. Work from anywhere.",
                workplace_type="remote",
            )
        )
    process_jobs(store)
    ensure_schema(store)
    profile = CandidateProfile(
        experience=[ProfileEntry(text="Built Python model training pipeline in a project")],
        skills=["Docker", "Git"],
        experience_years=1.25,
    )
    with store.db:
        store.db.execute(
            "INSERT INTO candidate_profiles(created_at,approved,profile_hash,profile) VALUES (?,?,?,?)",
            ("2026-10-29", 1, profile_hash(profile), profile_json(profile)),
        )
    factory = MagicMock()
    factory.return_value.__enter__.return_value.models.generate_content.side_effect = lambda **k: (
        MagicMock(
            text=json.dumps({"items": _classifications(extract_requirements(DESCRIPTION))}),
            usage_metadata=None,
        )
    )
    first = store.db.execute(
        "SELECT id FROM jobs WHERE json_extract(payload,'$.source_id')='assessed'"
    ).fetchone()[0]
    analyze(
        store,
        MatchingConfig(retries=0, max_requests_per_run=1),
        live=True,
        job_id=first,
        client_factory=factory,
    )
    return first


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-only")
    path = tmp_path / "jobs.sqlite3"
    with Store(path) as store:
        assessed = seed(store)
    app = Dashboard(path, MatchingConfig(), PORT)
    app.prepare()
    return app, path, assessed


def open_store(app, path):
    app.cache.wait()  # a background refresh holds the writer lock
    return Store(path)


def ids(app, path):
    app.cache.wait()
    with Store(path) as store:
        rows = store.db.execute(
            "SELECT id, json_extract(payload,'$.source_id') FROM jobs"
        ).fetchall()
    return {r[1]: r[0] for r in rows}


def assessments(path, app=None):
    if app:
        app.cache.wait()
    with Store(path) as store:
        return [tuple(r) for r in store.db.execute("SELECT * FROM ai_assessments ORDER BY id")]


def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("page rendering must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_overview_counts_come_from_the_database(env):
    app, path, assessed = env
    status, _, body = call(app, "GET", "/")
    assert status == 200
    stats = dict(
        (label, int(value)) for value, label in re.findall(r"<b>(\d+)</b><span>(.*?)</span>", body)
    )
    assert stats["Total discovered jobs"] == 4
    assert stats["Active and listed"] == 4
    assert stats["Current CV assessments"] == 1
    assert stats["Awaiting assessment"] == stats["Matching population"] - 1
    assert stats["Saved jobs"] == 0
    for heading in ("source state", "Module 2 eligibility", "technical match", "My applications"):
        assert heading.lower() in body.lower()


def test_explorer_defaults_filters_and_never_hides_unverified(env):
    app, path, assessed = env
    _, _, body = call(app, "GET", "/jobs")
    assert "Software Engineer, AI Coding Tasks" in body and "Backend Python Engineer" in body
    assert "Sales Manager" not in body  # not engineering-relevant by default
    assert "Senior Backend Engineer" not in body  # INELIGIBLE hidden by the default Module 2 filter
    _, _, everything = call(app, "GET", "/jobs?relevance=&listing=&eligibility=")
    for title in ("Sales Manager", "Senior Backend Engineer", "Backend Python Engineer"):
        assert title in everything
    _, _, only_unverified = call(app, "GET", "/jobs?eligibility=UNVERIFIED&relevance=")
    assert (
        "Backend Python Engineer" in only_unverified
        and "Senior Backend Engineer" not in only_unverified
    )
    _, _, ineligible = call(app, "GET", "/jobs?eligibility=INELIGIBLE&relevance=")
    assert "Senior Backend Engineer" in ineligible and "Backend Python Engineer" not in ineligible
    _, _, by_source = call(app, "GET", "/jobs?source=arbeitnow&relevance=&eligibility=")
    assert "Bigco" in by_source and "Plainco" not in by_source
    _, _, searched = call(app, "GET", "/jobs?q=plainco&relevance=&eligibility=")
    assert "Plainco" in searched and "Bigco" not in searched
    _, _, remote = call(app, "GET", "/jobs?workplace=onsite&relevance=&eligibility=")
    assert "Salesco" in remote and "Plainco" not in remote


def test_missing_score_is_not_zero_and_match_sort_lists_only_current(env):
    app, path, assessed = env
    _, _, body = call(app, "GET", "/jobs?q=plainco")
    assert "Not assessed" in body and "/100" not in body
    _, _, ranked = call(app, "GET", "/jobs?sort=match&relevance=&eligibility=&listing=")
    assert "Software Engineer, AI Coding Tasks" in ranked and "Plainco" not in ranked
    assert "only jobs with a current assessment" in ranked


def test_score_is_always_shown_with_confidence_coverage_and_ceiling(env):
    app, path, assessed = env
    _, _, body = call(app, "GET", f"/jobs/{assessed}")
    assert re.search(r"\d+/100", body)
    for needle in (
        "confidence",
        "Evidence coverage",
        "Score ceiling",
        "Applicable weight",
        "UNMATCHED",
        "UNVERIFIED",
    ):
        assert needle in body
    assert "evidence-limited" in body and "not an employment-eligibility decision" in body
    _, _, listing = call(app, "GET", "/jobs?assessment=current")
    assert re.search(r"coverage \d+%", listing) and "ceiling" in listing


def test_low_confidence_zero_score_is_not_a_hard_exclusion(env):
    app, path, assessed = env
    with open_store(app, path) as store:
        row = store.db.execute("SELECT score, result FROM ai_assessments").fetchone()
    assert json.loads(row[1])["confidence"]["level"] == "low"
    _, _, body = call(app, "GET", "/jobs")
    assert "Software Engineer, AI Coding Tasks" in body  # still listed by default
    _, _, detail = call(app, "GET", f"/jobs/{assessed}")
    assert "Save job" in detail and "Add a manual review" in detail and "Plan to apply" in detail
    _, _, ranking = call(app, "GET", "/rankings")
    assert "Software Engineer, AI Coding Tasks" in ranking
    assert "ranking among assessed jobs only" in ranking
    assert "Awaiting assessment" in ranking and "billable" in ranking


def test_stale_assessment_is_not_presented_as_current(env):
    app, path, assessed = env
    with open_store(app, path) as store:
        store.upsert(
            job(
                source="remotive",
                source_id="assessed",
                source_url="https://example.test/jobs/assessed",
                company="Askable",
                title="Software Engineer, AI Coding Tasks",
                description=DESCRIPTION + "\nA new requirement was added.",
                workplace_type="remote",
            )
        )
        process_jobs(store)
    status, _, body = call(app, "GET", f"/jobs/{assessed}")  # may serve the previous snapshot
    app.cache.warm()
    _, _, body = call(app, "GET", f"/jobs/{assessed}")
    assert "stale" in body.lower() and "historical and are not shown as current" in body
    assert not re.search(r"<b style='font-size:1.6rem'>\d+/100", body)
    _, _, current_only = call(app, "GET", "/jobs?assessment=current")
    assert "Askable" not in current_only
    _, _, stale_only = call(app, "GET", "/jobs?assessment=stale")
    assert "Askable" in stale_only
    assert len(assessments(path, app)) == 1  # the historical row is untouched


def test_job_detail_shows_every_module2_dimension_with_evidence(env):
    app, path, assessed = env
    jid = ids(app, path)["plain"]
    _, _, body = call(app, "GET", f"/jobs/{jid}")
    for label in (
        "Engineering relevance",
        "Experience",
        "Geography",
        "Employment restrictions",
        "Workplace",
    ):
        assert label in body
    assert "UNVERIFIED is not INELIGIBLE" in body
    assert "ELIGIBLE</span>" not in body.split("Module 2 screening")[0].replace("INELIGIBLE", "")


def test_review_queue_and_append_only_manual_reviews(env):
    app, path, assessed = env
    jid = ids(app, path)["plain"]
    _, _, queue = call(app, "GET", "/reviews")
    assert "Plainco" not in queue or "Backend Python Engineer" in queue
    assert "never shown as independently verified" in queue
    status, headers, _ = post(
        app,
        f"/jobs/{jid}/review",
        {
            "status": "evidence_supported",
            "explanation": "Posting says hires worldwide",
            "evidence": "https://example.test/careers",
            "next": f"/jobs/{jid}",
        },
    )
    assert status == 303 and "appended" in headers["Location"]
    post(
        app,
        f"/jobs/{jid}/review",
        {"status": "unresolved", "explanation": "Second look", "next": f"/jobs/{jid}"},
    )
    _, _, detail = call(app, "GET", f"/jobs/{jid}")
    assert "#1" in detail and "#2" in detail and "did not verify" in detail
    assert "UNVERIFIED" in detail
    with open_store(app, path) as store:
        assert store.db.execute("SELECT count(*) FROM eligibility_reviews").fetchone()[0] == 2
        status = json.loads(
            store.db.execute(
                "SELECT result FROM job_classifications WHERE job_id=?", (jid,)
            ).fetchone()[0]
        )["status"]
    assert status == "UNVERIFIED"  # a manual claim never upgrades Module 2
    bad, headers, _ = post(
        app,
        f"/jobs/{jid}/review",
        {"status": "evidence_supported", "explanation": "x", "next": f"/jobs/{jid}"},
    )
    assert "error=" in headers["Location"]  # evidence is required for an assertion


def test_application_tracking_flow_and_validation(env):
    app, path, assessed = env
    jid = ids(app, path)["plain"]
    _, headers, _ = post(app, f"/jobs/{jid}/save", {"next": f"/jobs/{jid}"})
    assert "notice=Saved" in headers["Location"]
    _, headers, _ = post(app, f"/jobs/{jid}/save", {"next": f"/jobs/{jid}"})
    assert "Already+tracked" in headers["Location"] or "Already%20tracked" in headers["Location"]
    _, headers, _ = post(
        app,
        f"/jobs/{jid}/status",
        {"status": "APPLIED", "applied_on": "2026-02-31", "next": f"/jobs/{jid}"},
    )
    assert "error=" in headers["Location"]
    post(
        app,
        f"/jobs/{jid}/status",
        {
            "status": "APPLIED",
            "applied_on": "2026-11-01",
            "note": "via site",
            "next": f"/jobs/{jid}",
        },
    )
    post(
        app,
        f"/jobs/{jid}/details",
        {
            "follow_up_on": "2026-11-20",
            "channel": "site",
            "reference": "https://example.test/apply",
            "notes": "<script>alert(1)</script> private",
            "next": f"/jobs/{jid}",
        },
    )
    _, _, detail = call(app, "GET", f"/jobs/{jid}")
    assert (
        "&lt;script&gt;alert(1)&lt;/script&gt; private" in detail and "<script>alert" not in detail
    )
    _, _, listing = call(app, "GET", "/applications")
    assert "Plainco" in listing and "APPLIED" in listing and "2026-11-20" in listing
    _, _, filtered = call(app, "GET", "/applications?status=OFFER")
    assert "No application matches" in filtered
    _, _, explorer = call(app, "GET", "/jobs?tracked=APPLIED&relevance=&eligibility=")
    assert "Plainco" in explorer and "Backend Python" in explorer
    _, headers, _ = post(
        app, f"/jobs/{jid}/details", {"reference": "javascript:alert(1)", "next": f"/jobs/{jid}"}
    )
    assert "error=" in headers["Location"]
    _, headers, _ = post(app, "/jobs/99999/save", {"next": "/"})
    assert "error=" in headers["Location"]


def test_applications_survive_restart_and_a_closed_job(env):
    app, path, assessed = env
    jid = ids(app, path)["plain"]
    post(
        app, f"/jobs/{jid}/status", {"status": "INTERVIEW", "applied_on": "2026-10-30", "next": "/"}
    )
    with open_store(app, path) as store:
        store.db.execute("UPDATE job_sources SET lifecycle_state='closed' WHERE job_id=?", (jid,))
        store.db.commit()
        store._refresh_job(jid)
    fresh = Dashboard(path, MatchingConfig(), PORT)
    fresh.prepare()
    _, _, body = call(fresh, "GET", "/applications")
    assert "INTERVIEW" in body and "Plainco" in body and "closed" in body
    assert "independent of your status" in body
    with open_store(app, path) as store:
        assert tracking.get(store.db, jid)["status"] == "INTERVIEW"


def test_empty_applications_page_points_to_saving_jobs(env):
    app, path, assessed = env
    _, _, body = call(app, "GET", "/applications")
    assert "No applications yet" in body and "/jobs" in body


def test_requests_are_local_and_forms_need_the_token(env):
    app, path, assessed = env
    jid = ids(app, path)["plain"]
    assert call(app, "GET", "/", host="evil.example:8765")[0] == 403
    assert call(app, "GET", "/", host="127.0.0.1:9999")[0] == 403
    assert call(app, "POST", f"/jobs/{jid}/save", {})[0] == 403  # no token
    assert call(app, "POST", f"/jobs/{jid}/save", {"csrf": "wrong"})[0] == 403
    assert post(app, f"/jobs/{jid}/save", {}, origin="https://evil.example")[0] == 403
    status, headers, _ = post(
        app, f"/jobs/{jid}/save", {"next": "//evil.example/x"}, origin=f"http://127.0.0.1:{PORT}"
    )
    assert status == 303 and headers["Location"].startswith("/applications")
    _, h, _ = call(app, "GET", "/")
    assert (
        "frame-ancestors 'none'" in h["Content-Security-Policy"]
        and h["Cache-Control"] == "no-store"
    )
    assert call(app, "DELETE", "/jobs/1")[0] == 400


def test_pages_never_call_gemini_collectors_or_the_network(env, monkeypatch):
    app, path, assessed = env
    before = assessments(path)
    no_network(monkeypatch)
    import job_scraper.matching as matching

    def forbidden(*args, **kwargs):
        raise AssertionError("page rendering must not call Gemini")

    monkeypatch.setattr(matching.genai, "Client", forbidden)
    jid = ids(app, path)["plain"]
    for target in (
        "/",
        "/jobs",
        "/jobs?q=x",
        "/jobs?sort=match",
        f"/jobs/{jid}",
        f"/jobs/{assessed}",
        "/rankings",
        "/rankings?eligibility=UNVERIFIED&confidence=low",
        "/reviews",
        "/reviews?dimension=geography",
        "/applications",
    ):
        assert call(app, "GET", target)[0] == 200, target
    post(app, f"/jobs/{jid}/save", {"next": "/"})
    assert assessments(path, app) == before


def test_untrusted_text_and_links_are_escaped_or_refused():
    assert render.e("<img src=x onerror=1>") == "&lt;img src=x onerror=1&gt;"
    assert render.link_url("javascript:alert(1)") == '<span class="mute">—</span>'
    assert render.link_url("https://example.com/a").startswith(
        '<a href="https://example.com/a" target="_blank" rel="noopener noreferrer nofollow">'
    )
    assert render.eligibility_badge("UNVERIFIED").count(
        "b-warn"
    ) == 1 and "b-bad" not in render.eligibility_badge("UNVERIFIED")
    assert "b-ok" not in render.eligibility_badge("UNVERIFIED")


def test_pagination_is_server_side(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-only")
    path = tmp_path / "jobs.sqlite3"
    with Store(path) as store:
        seed(store, count=30)
    app = Dashboard(path, MatchingConfig(), PORT)
    app.prepare()
    _, _, first = call(app, "GET", "/jobs?q=bulk&sort=company")
    _, _, second = call(app, "GET", "/jobs?q=bulk&sort=company&page=2")
    assert first.count("<tr><td><a href") == data.PAGE_SIZE and second.count("<tr><td><a href") == 5
    assert "Page 1 of 2" in first and "Page 2 of 2" in second
    _, _, beyond = call(app, "GET", "/jobs?q=bulk&page=99")
    assert "Page 2 of 2" in beyond
    _, _, junk = call(app, "GET", "/jobs?page=abc&sort=nope")
    assert "Job Explorer" in junk


def test_dashboard_refuses_non_loopback_binding(tmp_path):
    from job_scraper.dashboard.app import serve

    with pytest.raises(ValueError, match="loopback"):
        serve(tmp_path / "x.sqlite3", MatchingConfig(), host="0.0.0.0")
