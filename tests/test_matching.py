import json
from unittest.mock import MagicMock

import pytest
from google.genai import errors

from job_scraper import candidate
from job_scraper.candidate import CandidateProfile, ProfileEntry, profile_hash, profile_json
from job_scraper.config import MatchingConfig
from job_scraper.matching import (
    GEMINI_RESPONSE_SCHEMA,
    AnalysisSignals,
    Finding,
    GeminiFindings,
    Requirement,
    _safe_label,
    _signals_from_findings,
    analyze,
    approve_profile,
    candidates,
    ensure_schema,
    export_profile,
    latest_profile,
    results,
    score,
    validate_evidence,
)
from job_scraper.models import Job
from job_scraper.processing import process_jobs
from job_scraper.storage import Store


def _profile():
    return CandidateProfile(
        experience=[
            ProfileEntry(text="Software engineer using Python APIs", dates=["2023", "2025"])
        ],
        skills=["Python"],
        technologies=["Python"],
        experience_years=1.25,
    )


def _job(source_id="one", description=None):
    return Job(
        source="remotive",
        source_id=source_id,
        company="Synthetic Co",
        title="Python Software Engineer",
        description=description
        or (
            "Build Python APIs and deploy services. Required Python. "
            "1 year experience. Fully remote."
        ),
        workplace_type="remote",
        source_url=f"https://example.com/jobs/{source_id}",
        attribution="Synthetic",
        attribution_url="https://example.com",
    )


def _signals():
    return AnalysisSignals(
        required_skills=[
            Requirement(
                text="Python", job_excerpt="Required Python", matched=True, profile_excerpt="Python"
            )
        ],
        preferred_skills_projects=[],
        experience_requirements=[
            Requirement(
                text="1 year experience",
                job_excerpt="1 year experience",
                matched=True,
                profile_excerpt="Software engineer",
            )
        ],
        responsibilities=[
            Requirement(
                text="Build Python APIs",
                job_excerpt="Build Python APIs",
                matched=True,
                profile_excerpt="Software engineer using Python APIs",
            )
        ],
        matching_qualifications=[],
        transferable_experience=[],
        missing_qualifications=[],
        eligibility_evidence=[],
        unresolved_requirements=[],
    )


def _findings():
    return GeminiFindings(
        findings=[
            Finding(
                kind="req",
                text="Python",
                job_excerpt="Required Python",
                matched=True,
                profile_excerpt="Python",
            ),
            Finding(
                kind="exp",
                text="1 year experience",
                job_excerpt="1 year experience",
                matched=True,
                profile_excerpt="Software engineer",
            ),
            Finding(
                kind="duty",
                text="Build Python APIs",
                job_excerpt="Build Python APIs",
                matched=True,
                profile_excerpt="Software engineer using Python APIs",
            ),
        ]
    )


def _referenced_response(*, bad_claim=False):
    def generate_content(**kwargs):
        sent = json.loads(kwargs["contents"])
        items = sent["requirements"]
        assignments = []
        for item in items:
            parts = []
            if "Required Python" in item["text"]:
                parts = [
                    {"kind": "req"},
                    {"kind": "exp"},
                    {"kind": "duty"},
                ]
                if bad_claim:
                    parts[0]["skills"] = ["Python Universe"]
            else:
                parts = [{"kind": "nonreq"}]
            assignments.append({"requirement_id": item["id"], "parts": parts})
        return MagicMock(text=json.dumps({"items": assignments}), usage_metadata=None)

    return generate_content


def _approve(store, tmp_path):
    ensure_schema(store)
    profile = _profile()
    with store.db:
        store.db.execute(
            "INSERT INTO candidate_profiles(created_at,approved,profile_hash,profile) VALUES (?,?,?,?)",
            ("2026-01-01", 0, profile_hash(profile), profile_json(profile)),
        )
    review = tmp_path / "review.json"
    export_profile(store, review)
    return approve_profile(store, review)


def test_pdf_extraction_is_local_sanitized_and_has_explicit_dates(monkeypatch, tmp_path):
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4 synthetic")
    page = MagicMock()
    page.extract_text.return_value = """Jane Doe
    jane@example.com | +1 555 123 4567
    123 Main Street
    Experience
    Software engineer using Python, Jan 2023 - Present
    Skills
    Python, Django, SQL
    Projects
    Built API with Django in 2024
    Education
    BS Computer Science 2022
    """
    reader = MagicMock(is_encrypted=False, pages=[page])
    monkeypatch.setattr(candidate, "PdfReader", lambda _: reader)
    profile = candidate.extract_pdf(pdf)
    content = profile_json(profile)
    assert "Jane Doe" not in content
    assert "jane@example.com" not in content
    assert "555 123" not in content
    assert "Main Street" not in content
    assert "Jan 2023" in profile.experience[0].dates
    assert profile.experience_years is None
    assert "Python" in profile.technologies


def test_profile_approval_requires_review_and_rejects_pii(tmp_path):
    with Store(":memory:") as store:
        ensure_schema(store)
        review = tmp_path / "initial.json"
        review.write_text(profile_json(_profile()), encoding="utf-8")
        with pytest.raises(ValueError, match="Import and review"):
            approve_profile(store, review)
        _approve(store, tmp_path)
        assert latest_profile(store, approved_only=True)["approved"]
        malicious = tmp_path / "bad.json"
        data = _profile().model_dump()
        data["skills"].append("person@example.com")
        malicious.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError, match="contact or address"):
            approve_profile(store, malicious)


def test_scoring_math_and_evidence_validation():
    signals = _signals()
    assert _signals_from_findings(_findings()) == signals
    assert score(signals, MatchingConfig())["score"] == 85
    validate_evidence(signals, _job().description, profile_json(_profile()))
    signals.required_skills[0].profile_excerpt = "invented qualification"
    with pytest.raises(ValueError, match="Unsupported profile"):
        validate_evidence(signals, _job().description, profile_json(_profile()))


def test_safe_evidence_diagnostics_redact_contact_like_values():
    assert _safe_label("Python") == "Python"
    assert _safe_label("person@example.com") == "redacted"
    assert _safe_label("P-" + "a" * 12, "P") == "P-" + "a" * 12
    assert _safe_label("P-person@example.com", "P") == "invalid_format"


def test_evidence_handles_line_breaks_without_joining_fields():
    signals = _signals()
    signals.required_skills[0].job_excerpt = "Required Python"
    job = json.dumps({"description": "Required\nPython. Build Python APIs. 1 year experience."})
    validate_evidence(signals, job, profile_json(_profile()))
    split_fields = json.dumps(
        {"a": "Required", "b": "Python. Build Python APIs. 1 year experience."}
    )
    with pytest.raises(ValueError, match="Unsupported job evidence"):
        validate_evidence(signals, split_fields, profile_json(_profile()))


def test_fractional_scoring_and_weight_validation():
    signals = _signals()
    signals.required_skills.append(
        Requirement(text="Ruby", job_excerpt="Required Ruby", matched=False, profile_excerpt=None)
    )
    assert score(signals, MatchingConfig())["score"] == 65
    with pytest.raises(ValueError, match="total 100"):
        MatchingConfig(weights={"required_skills": 30})


def test_queue_separates_eligibility_and_lifecycle():
    with Store(":memory:") as store:
        store.upsert(_job())
        store.upsert(_job("us", "Must be based in the US. Build Python APIs."))
        process_jobs(store)
        queue = candidates(store)
        assert {r["job_id"] for r in queue} == {1}
        assert queue[0]["eligibility_status"] == "UNVERIFIED"
        store.db.execute("UPDATE job_sources SET lifecycle_state='unknown' WHERE source_id='one'")
        assert candidates(store) == []
        history = candidates(store, include_history=True)
        assert history[0]["lifecycle"] == "unknown"
        assert history[0]["historical"]
        hidden = _job("hidden")
        hidden.is_listed = False
        store.upsert(hidden)
        process_jobs(store)
        assert all(
            row["source_url"] != str(hidden.source_url)
            for row in candidates(store, include_history=True)
        )


def test_prefilter_keeps_plausible_role_without_module2_category():
    with Store(":memory:") as store:
        store.upsert(_job("generic", "Design and build data pipelines for analytics."))
        with store.db:
            store.db.execute(
                "UPDATE jobs SET payload=json_set(payload,'$.title','Member of Technical Staff')"
            )
        process_jobs(store)
        store.db.execute(
            "UPDATE job_classifications SET result=json_set(result,'$.categories',json('[]'),'$.status','UNVERIFIED')"
        )
        assert candidates(store)


def test_offline_default_and_live_cache_staleness(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    with Store(":memory:") as store:
        store.upsert(_job())
        process_jobs(store)
        _approve(store, tmp_path)
        client = MagicMock()
        client.return_value.__enter__.return_value.models.generate_content.side_effect = (
            _referenced_response()
        )
        factory = client
        cfg = MatchingConfig(max_requests_per_run=1, retries=0)
        assert analyze(store, cfg, client_factory=factory)["requests"] == 0
        first = analyze(store, cfg, live=True, client_factory=factory)
        assert first["saved"] == 1
        assert first["requests"] == 1
        call_config = (
            factory.return_value.__enter__.return_value.models.generate_content.call_args.kwargs[
                "config"
            ]
        )
        assert call_config.response_json_schema == GEMINI_RESPONSE_SCHEMA
        assert call_config.response_schema is None
        assert results(store, cfg)[0]["score"] == 85
        assert results(store, cfg)[0]["eligibility_status"] == "UNVERIFIED"
        again = analyze(store, cfg, live=True, client_factory=factory)
        assert again["cached"] == 1 and again["requests"] == 0
        store.upsert(_job(description=_job().description + " More Python work."))
        assert results(store, cfg)[0]["stale"]
        changed = analyze(store, cfg, live=True, client_factory=factory)
        assert changed["saved"] == 1
        assert store.db.execute("SELECT count(*) FROM ai_assessments").fetchone()[0] == 2
        reweighted = MatchingConfig(
            max_requests_per_run=1,
            retries=0,
            weights={
                "required_skills": 50,
                "experience": 20,
                "role_alignment": 20,
                "preferred_projects": 10,
            },
        )
        assert results(store, reweighted)[0]["stale"]
        assert analyze(store, reweighted, live=True, client_factory=factory)["saved"] == 1
        revised = tmp_path / "revised.json"
        data = _profile().model_dump()
        data["skills"].append("SQL")
        revised.write_text(json.dumps(data), encoding="utf-8")
        approve_profile(store, revised)
        assert results(store, reweighted)[0]["stale"]
        with store.db:
            store.db.execute(
                "INSERT INTO candidate_profiles(created_at,approved,profile_hash,profile) VALUES (?,?,?,?)",
                ("2026-01-02", 0, profile_hash(_profile()), profile_json(_profile())),
            )
        with pytest.raises(ValueError, match="newer draft"):
            analyze(store, reweighted, live=True, client_factory=factory)


def test_api_failure_and_invalid_output_do_not_save(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    with Store(":memory:") as store:
        store.upsert(_job())
        process_jobs(store)
        _approve(store, tmp_path)
        factory = MagicMock()
        method = factory.return_value.__enter__.return_value.models.generate_content
        method.side_effect = errors.APIError(429, {"error": {"message": "synthetic-key"}})
        failed = analyze(store, MatchingConfig(retries=0), live=True, client_factory=factory)
        assert failed["failures"][0]["error_category"] == "quota"
        assert "synthetic-key" not in str(failed)
        method.side_effect = None
        method.return_value.text = '{"required_skills": []}'
        invalid = analyze(store, MatchingConfig(retries=0), live=True, client_factory=factory)
        assert invalid["failures"][0]["error_category"] == "invalid_response"
        assert invalid["failures"][0]["failure_stage"] == "schema_validation"
        method.side_effect = _referenced_response(bad_claim=True)
        rejected = analyze(store, MatchingConfig(retries=0), live=True, client_factory=factory)
        assert rejected["failures"][0]["failure_stage"] == "schema_validation"
        method.side_effect = None
        method.return_value.text = '{"items": []}'
        incomplete = analyze(store, MatchingConfig(retries=0), live=True, client_factory=factory)
        assert incomplete["failures"][0]["reason"] == "missing_requirement_id"
        assert store.db.execute("SELECT count(*) FROM ai_assessments").fetchone()[0] == 0


def test_rate_limit_stops_batch(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    with Store(":memory:") as store:
        store.upsert(_job("one"))
        store.upsert(_job("two"))
        process_jobs(store)
        _approve(store, tmp_path)
        factory = MagicMock()
        factory.return_value.__enter__.return_value.models.generate_content.side_effect = (
            errors.APIError(429, {"error": {"message": "sensitive"}})
        )
        out = analyze(
            store,
            MatchingConfig(max_requests_per_run=5, retries=0),
            live=True,
            client_factory=factory,
        )
        assert out["requests"] == 1
        assert len(out["failures"]) == 1
        assert store.db.execute("SELECT count(*) FROM ai_assessments").fetchone()[0] == 0


def test_later_api_failure_preserves_earlier_assessment(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    with Store(":memory:") as store:
        store.upsert(_job("one"))
        store.upsert(_job("two"))
        process_jobs(store)
        _approve(store, tmp_path)
        good = _referenced_response()
        factory = MagicMock()
        calls = 0

        def respond(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return good(**kwargs)
            raise errors.APIError(503, {"error": {"message": "sensitive"}})

        factory.return_value.__enter__.return_value.models.generate_content.side_effect = respond
        out = analyze(
            store,
            MatchingConfig(max_requests_per_run=2, retries=0),
            live=True,
            client_factory=factory,
        )
        assert out["saved"] == 1 and len(out["failures"]) == 1
        assert store.db.execute("SELECT count(*) FROM ai_assessments").fetchone()[0] == 1


def test_continued_experience_keeps_one_approved_duration(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    profile = CandidateProfile(
        experience=[
            ProfileEntry(
                text="Software engineer using Python APIs at Acme, 2025-Present",
                dates=["2025", "Present"],
            ),
            ProfileEntry(
                text="Software engineer using Python APIs at Acme, 2025-Present "
                "(continued responsibilities): built APIs",
                dates=["2025", "Present"],
            ),
        ],
        skills=["Python"],
        technologies=["Python"],
        experience_years=1.25,
    )
    with Store(":memory:") as store:
        store.upsert(_job())
        process_jobs(store)
        ensure_schema(store)
        with store.db:
            store.db.execute(
                "INSERT INTO candidate_profiles(created_at,approved,profile_hash,profile) VALUES (?,?,?,?)",
                ("2026-10-27", 1, profile_hash(profile), profile_json(profile)),
            )
        factory = MagicMock()
        method = factory.return_value.__enter__.return_value.models.generate_content
        method.side_effect = _referenced_response()
        out = analyze(store, MatchingConfig(retries=0), live=True, limit=1, client_factory=factory)
        assert out["saved"] == 1
        request = method.call_args.kwargs
        sent = json.loads(request["contents"])
        assert list(sent) == ["requirements"]
        assert "profile_evidence" not in sent
        assert "Python performs those decisions locally" in request["config"].system_instruction
        assert store.db.execute("SELECT score FROM ai_assessments").fetchone()[0] == 85


def test_job_only_pipeline_catalog_and_cache(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    profile = CandidateProfile(
        experience=[ProfileEntry(text="Built Python APIs professionally", dates=["2025"])],
        projects=[ProfileEntry(text="Created Rails APIs in a project", dates=["2024"])],
        skills=["Python", "Ruby on Rails"],
        experience_years=1.25,
    )
    description = (
        "Required Python and Docker.\nPreferred Rails.\n"
        "2+ years of professional software engineering.\nBuild Python APIs."
    )
    with Store(":memory:") as store:
        store.upsert(_job(description=description))
        process_jobs(store)
        ensure_schema(store)
        with store.db:
            store.db.execute(
                "INSERT INTO candidate_profiles(created_at,approved,profile_hash,profile) "
                "VALUES (?,?,?,?)",
                ("2026-10-27", 1, profile_hash(profile), profile_json(profile)),
            )
        tables = (
            "jobs",
            "job_sources",
            "job_classifications",
            "job_classification_history",
            "candidate_profiles",
        )
        before = {
            name: store.db.execute(f"SELECT * FROM {name} ORDER BY rowid").fetchall()
            for name in tables
        }

        def response(**kwargs):
            sent = json.loads(kwargs["contents"])
            assert list(sent) == ["requirements"]
            assignments = []
            for item in sent["requirements"]:
                text = item["text"]
                if "Required Python" in text:
                    parts = [{"kind": "req"}]
                elif "Preferred Rails" in text:
                    parts = [{"kind": "pref"}]
                elif "2+ years" in text:
                    parts = [{"kind": "exp"}]
                elif "Build Python" in text:
                    parts = [{"kind": "duty"}]
                else:
                    parts = [{"kind": "nonreq"}]
                assignments.append({"requirement_id": item["id"], "parts": parts})
            return MagicMock(text=json.dumps({"items": assignments}), usage_metadata=None)

        factory = MagicMock()
        method = factory.return_value.__enter__.return_value.models.generate_content
        method.side_effect = response
        config = MatchingConfig(max_requests_per_run=1, retries=0)
        out = analyze(store, config, live=True, job_id=1, client_factory=factory)
        assert out["saved"] == 1 and out["requests"] == 1
        assert all(
            store.db.execute(f"SELECT * FROM {name} ORDER BY rowid").fetchall() == rows
            for name, rows in before.items()
        )
        cached = analyze(store, config, live=True, job_id=1, client_factory=factory)
        assert cached["cached"] == 1 and cached["requests"] == 0
        assert method.call_count == 1
        saved = json.loads(store.db.execute("SELECT result FROM ai_assessments").fetchone()[0])
        assert saved["score"] == 55
        assert [x["match_state"] for x in saved["referenced_findings"]] == [
            "MATCHED",
            "UNMATCHED",
            "MATCHED",
            "UNMATCHED",
            "MATCHED",
        ]
        assert saved["referenced_findings"][2]["skill_support_level"] == "project"
        assert saved["signals"]["experience_requirements"][0]["matched"] is False


def test_job_only_schema_rejects_candidate_claims(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    with Store(":memory:") as store:
        store.upsert(_job())
        process_jobs(store)
        ensure_schema(store)
        profile = _profile()
        with store.db:
            store.db.execute(
                "INSERT INTO candidate_profiles(created_at,approved,profile_hash,profile) "
                "VALUES (?,?,?,?)",
                ("2026-10-27", 1, profile_hash(profile), profile_json(profile)),
            )

        def response(**kwargs):
            item = json.loads(kwargs["contents"])["requirements"][0]
            return MagicMock(
                text=json.dumps(
                    {
                        "items": [
                            {
                                "requirement_id": item["id"],
                                "parts": [{"kind": "req"}],
                                "candidate_skill_id": "S-invented",
                            }
                        ]
                    }
                ),
                usage_metadata=None,
            )

        factory = MagicMock()
        factory.return_value.__enter__.return_value.models.generate_content.side_effect = response
        out = analyze(store, MatchingConfig(retries=0), live=True, client_factory=factory)
        assert out["saved"] == 0
        assert out["failures"][0]["failure_stage"] == "schema_validation"
        assert store.db.execute("SELECT count(*) FROM ai_assessments").fetchone()[0] == 0


def test_long_description_is_not_silently_truncated_for_live_extraction(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    with Store(":memory:") as store:
        store.upsert(_job(description="Required Python. Build Python APIs. " * 400))
        process_jobs(store)
        _approve(store, tmp_path)
        factory = MagicMock()
        out = analyze(store, MatchingConfig(retries=0), live=True, client_factory=factory)
        assert out["requests"] == 0 and out["saved"] == 0
        assert out["failures"][0]["reason"] == "description_exceeds_limit"
        factory.assert_not_called()
