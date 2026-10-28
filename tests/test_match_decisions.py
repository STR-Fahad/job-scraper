"""Gemini proposes references; Python alone decides states and weighted scores."""

import hashlib

import pytest

from job_scraper.config import MatchingConfig
from job_scraper.evidence import SCHEMA, EvidenceError, ReferencedResponse, build_segments, resolve
from job_scraper.matching import score_resolved


def _fixture():
    job = {
        "title": "Engineer",
        "description": (
            "Required Python.\nPreferred Rails.\n2+ years of professional experience.\n"
            "Build Python APIs."
        ),
    }
    profile = {
        "experience": [{"text": "Built Python APIs professionally", "dates": ["2025"]}],
        "projects": [{"text": "Created Rails APIs", "dates": ["2024"]}],
        "skills": ["Python", "Ruby on Rails"],
        "technologies": [],
        "education": [],
        "certifications": [],
        "experience_years": 1.25,
    }
    jobs, people = build_segments(job, profile)
    return jobs, people


def _id(segments, phrase):
    return next(item.id for item in segments if phrase in item.text)


def _finding(kind, text, job_id, profile_id=None, skill=None, **extra):
    return {
        "kind": kind,
        "text": text,
        "job_evidence_id": job_id,
        "profile_evidence_id": profile_id,
        "candidate_skill_id": (
            "S-" + hashlib.sha256(skill.encode()).hexdigest()[:12] if skill else None
        ),
        "explanation": "Model interpretation; ignored for factual validation.",
        **extra,
    }


def _resolve(jobs, people, findings):
    return resolve(ReferencedResponse(findings=findings), jobs, people, 1.25)


def test_gemini_schema_omits_match_flag_and_python_derives_all_states():
    fields = SCHEMA["properties"]["findings"]["items"]["properties"]
    assert "matched" not in fields
    assert "candidate_skill_id" not in fields
    assert "profile_evidence_id" not in fields
    jobs, people = _fixture()
    findings = [
        _finding(
            "req",
            "Python",
            _id(jobs, "Required Python"),
            _id(people, "Built Python"),
            "python",
            matched=False,
        ),
        _finding(
            "pref",
            "Rails",
            _id(jobs, "Preferred Rails"),
            _id(people, "Created Rails"),
            "ruby on rails",
        ),
        _finding("exp", "2+ years", _id(jobs, "2+ years")),
        _finding(
            "duty", "Build Python APIs", _id(jobs, "Build Python APIs"), _id(people, "Built Python")
        ),
    ]
    result = _resolve(jobs, people, findings)
    assert [item["match_state"] for item in result] == [
        "MATCHED",
        "MATCHED",
        "UNMATCHED",
        "MATCHED",
    ]
    assert "ignored_legacy_matched_flag" in result[0]["derived_fields"]
    assert result[2]["matched"] is False
    assert score_resolved(result, MatchingConfig())["score"] == 75


def test_missing_skill_id_is_derived_only_for_valid_profile_reference():
    jobs, people = _fixture()
    finding = _finding("req", "Python", _id(jobs, "Required Python"), _id(people, "Built Python"))
    result = _resolve(jobs, people, [finding, _finding("exp", "2+ years", _id(jobs, "2+ years"))])
    assert result[0]["match_state"] == "MATCHED"
    assert "candidate_skill_id_from_catalog" in result[0]["derived_fields"]
    finding["profile_evidence_id"] = _id(people, "Created Rails")
    with pytest.raises(EvidenceError, match="wrong_skill_evidence"):
        _resolve(jobs, people, [finding])


def test_required_project_skill_is_partial_and_experience_is_not_inflated():
    jobs, people = _fixture()
    result = _resolve(
        jobs,
        people,
        [
            _finding(
                "req",
                "Rails",
                _id(jobs, "Preferred Rails"),
                _id(people, "Created Rails"),
                "ruby on rails",
            ),
            _finding("exp", "2+ years", _id(jobs, "2+ years")),
            _finding("pref", "Rails", _id(jobs, "Preferred Rails")),
            _finding("duty", "Build Python APIs", _id(jobs, "Build Python APIs")),
        ],
    )
    assert [item["match_state"] for item in result[:3]] == ["PARTIAL", "UNMATCHED", "UNVERIFIED"]
    assert score_resolved(result, MatchingConfig())["breakdown"]["required_skills"]["points"] == 20


def test_response_skipping_too_much_weight_is_rejected():
    jobs, people = _fixture()
    result = _resolve(
        jobs,
        people,
        [
            _finding(
                "req", "Python", _id(jobs, "Required Python"), _id(people, "Built Python"), "python"
            ),
            _finding("exp", "2+ years", _id(jobs, "2+ years")),
        ],
    )
    with pytest.raises(EvidenceError, match="incomplete_job_requirements"):
        score_resolved(result, MatchingConfig())


def test_transferable_evidence_can_only_upgrade_role_to_partial():
    jobs, people = _fixture()
    duty_id = _id(jobs, "Build Python APIs")
    result = _resolve(
        jobs,
        people,
        [
            _finding(
                "req", "Python", _id(jobs, "Required Python"), _id(people, "Built Python"), "python"
            ),
            _finding("exp", "2+ years", _id(jobs, "2+ years")),
            _finding("duty", "Build Python APIs", duty_id),
            _finding("transfer", "Build Python APIs", duty_id, _id(people, "Built Python")),
        ],
    )
    assert result[2]["match_state"] == "PARTIAL"
    assert result[2]["skill_support_level"] == "transferable"
    assert score_resolved(result, MatchingConfig())["breakdown"]["role_alignment"]["points"] == 10


def test_missing_reference_remains_unverified_and_cannot_make_score():
    _, people = _fixture()
    jobs, _ = build_segments({"title": "Engineer", "description": "Required Python."}, {})
    result = _resolve(
        jobs,
        people,
        [
            _finding("req", "Python", _id(jobs, "Required Python")),
        ],
    )
    assert result[0]["match_state"] == "UNVERIFIED"
    with pytest.raises(EvidenceError, match="incomplete_job_requirements"):
        score_resolved(result, MatchingConfig())


def test_broad_preference_without_evidence_stays_unverified():
    jobs, people = build_segments(
        {"title": "Engineer", "description": "Deep expertise in engineering."},
        {
            "experience": [],
            "projects": [],
            "skills": [],
            "technologies": [],
            "education": [],
            "certifications": [],
            "experience_years": 1.25,
        },
    )
    result = _resolve(
        jobs,
        people,
        [
            _finding("pref", "Deep expertise", _id(jobs, "Deep expertise")),
        ],
    )
    assert result[0]["match_state"] == "UNVERIFIED"


def test_project_technology_list_alone_cannot_confirm_role_alignment():
    jobs, people = build_segments(
        {"title": "Engineer", "description": "Build Python APIs."},
        {
            "experience": [],
            "projects": [{"text": "Research | Python, REST APIs. Wrote a report.", "dates": []}],
            "skills": ["Python", "REST APIs"],
            "technologies": [],
            "education": [],
            "certifications": [],
            "experience_years": 1.25,
        },
    )
    result = _resolve(
        jobs,
        people,
        [
            _finding(
                "duty",
                "Build Python APIs",
                _id(jobs, "Build Python APIs"),
                _id(people, "Research | Python"),
            ),
        ],
    )
    assert result[0]["match_state"] == "UNVERIFIED"
    with pytest.raises(EvidenceError, match="incomplete_job_requirements"):
        score_resolved(result, MatchingConfig())


@pytest.mark.parametrize("invalid", ["unknown_profile", "unrelated_profile", "invented_skill"])
def test_false_references_are_still_rejected(invalid):
    jobs, people = _fixture()
    profile_id = _id(people, "Built Python")
    skill = "python"
    if invalid == "unknown_profile":
        profile_id = "P-000000000000"
    elif invalid == "unrelated_profile":
        profile_id = _id(people, "Created Rails")
    else:
        skill = "kubernetes"
    finding = _finding("req", "Python", _id(jobs, "Required Python"), profile_id, skill)
    with pytest.raises(EvidenceError):
        _resolve(jobs, people, [finding])
