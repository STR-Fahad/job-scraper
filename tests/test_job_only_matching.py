"""Job-side model output cannot choose candidate qualifications or citations."""

import pytest
from pydantic import ValidationError

from job_scraper.candidate import CandidateProfile, ProfileEntry
from job_scraper.config import MatchingConfig
from job_scraper.evidence import (
    EvidenceError,
    JobResponse,
    build_segments,
    build_skill_catalog,
    resolve_job_requirements,
)
from job_scraper.matching import score_resolved


def _case():
    profile = CandidateProfile(
        experience=[ProfileEntry(text="Built Ruby on Rails APIs and React UI professionally")],
        projects=[
            ProfileEntry(
                text="DeepFake Detection | Python, PyTorch. Built Python model training pipeline"
            )
        ],
        skills=["Docker", "Ruby on Rails", "Python", "PyTorch"],
        experience_years=1.25,
    )
    job = {
        "title": "Software Engineer",
        "description": "Required Rails, Python, Docker and Go.\n"
        "2+ years of engineering experience.\nBuild Rails APIs and React UI.",
    }
    jobs, people = build_segments(job, profile.model_dump())
    by_text = {x.text: x.id for x in jobs if x.field.startswith("description")}
    findings = [
        {
            "kind": "req",
            "text": skill,
            "job_evidence_id": by_text["Required Rails, Python, Docker and Go."],
        }
        for skill in ("Rails", "Python", "Docker", "Go")
    ]
    findings += [
        {
            "kind": "exp",
            "text": "2+ years",
            "job_evidence_id": by_text["2+ years of engineering experience."],
        },
        {
            "kind": "duty",
            "text": "Build Rails APIs and React UI",
            "job_evidence_id": by_text["Build Rails APIs and React UI."],
        },
    ]
    return jobs, people, findings


def test_python_chooses_alias_project_listed_and_missing_support():
    jobs, people, findings = _case()
    result = resolve_job_requirements(
        JobResponse.model_validate({"findings": findings}), jobs, people, 1.25
    )
    assert [x["match_state"] for x in result] == [
        "MATCHED",
        "PARTIAL",
        "UNVERIFIED",
        "UNMATCHED",
        "UNMATCHED",
        "MATCHED",
    ]
    assert result[0]["skill_support_level"] == "professional"
    assert result[1]["skill_support_level"] == "project"
    assert result[2]["skill_support_level"] == "listed"
    assert result[4]["profile_evidence_id"] is None
    assert score_resolved(result, MatchingConfig())["score"] == 35


def test_complete_low_match_scores_zero_instead_of_failing():
    jobs, people, findings = _case()
    unrelated = CandidateProfile(experience_years=0.5)
    _, people = build_segments({"description": "placeholder"}, unrelated.model_dump())
    result = resolve_job_requirements(
        JobResponse.model_validate({"findings": findings}), jobs, people, 0.5
    )
    scored = score_resolved(result, MatchingConfig())
    assert scored["score"] == 0
    assert scored["breakdown"]["required_skills"]["unmatched"] == 4
    assert scored["breakdown"]["experience"]["unmatched"] == 1


def test_complete_listed_only_and_ambiguous_skills_score_zero():
    jobs, _, findings = _case()
    profile = CandidateProfile(skills=["Rails", "Python", "Docker"], experience_years=1.25)
    _, people = build_segments({"description": "placeholder"}, profile.model_dump())
    result = resolve_job_requirements(
        JobResponse.model_validate({"findings": findings}), jobs, people, 1.25
    )
    scored = score_resolved(result, MatchingConfig())
    assert scored["score"] == 0
    assert scored["breakdown"]["required_skills"]["unverified"] == 3
    assert result[0]["skill_support_level"] == "listed"


def test_complete_high_match_retains_weights():
    jobs, _, findings = _case()
    profile = CandidateProfile(
        experience=[
            ProfileEntry(
                text="Built Rails APIs and React UI; developed Python, Docker and Go services"
            )
        ],
        skills=["Go"],
        experience_years=3,
    )
    _, people = build_segments({"description": "placeholder"}, profile.model_dump())
    result = resolve_job_requirements(
        JobResponse.model_validate({"findings": findings}), jobs, people, 3
    )
    scored = score_resolved(result, MatchingConfig())
    assert scored["score"] >= 80
    assert scored["breakdown"]["required_skills"]["weight"] == 40
    assert scored["breakdown"]["experience"]["weight"] == 25
    assert scored["breakdown"]["role_alignment"]["weight"] == 20
    assert scored["breakdown"]["preferred_projects"]["weight"] == 15


def test_model_candidate_ids_and_flags_are_rejected():
    jobs, people, findings = _case()
    for extra in ("candidate_skill_id", "profile_evidence_id", "matched"):
        bad = [dict(x) for x in findings]
        bad[0][extra] = "invented" if extra != "matched" else True
        with pytest.raises(ValidationError):
            JobResponse.model_validate({"findings": bad})


def test_wrong_job_reference_and_incomplete_requirements_rejected():
    jobs, people, findings = _case()
    bad = [dict(x) for x in findings]
    bad[0]["job_evidence_id"] = "J-invented"
    with pytest.raises(EvidenceError, match="unknown_id"):
        resolve_job_requirements(JobResponse.model_validate({"findings": bad}), jobs, people, 1.25)
    without_experience = [x for x in findings if x["kind"] != "exp"]
    with pytest.raises(EvidenceError, match="missing_experience_requirement"):
        resolve_job_requirements(
            JobResponse.model_validate({"findings": without_experience}), jobs, people, 1.25
        )
    without_duties = [x for x in findings if x["kind"] != "duty"]
    with pytest.raises(EvidenceError, match="missing_responsibilities"):
        resolve_job_requirements(
            JobResponse.model_validate({"findings": without_duties}), jobs, people, 1.25
        )


def test_catalog_does_not_equate_distinct_technologies():
    _, people, _ = _case()
    names = {item.name for item in build_skill_catalog(people)}
    assert "ruby on rails" in names and "python" in names
    assert "go" not in names


def test_alternative_skill_clause_does_not_assume_candidate_choice():
    profile = CandidateProfile(skills=["Python"], experience_years=1.25)
    jobs, people = build_segments(
        {"description": "Strong in at least one of Python, Go or Rust. Build APIs."},
        profile.model_dump(),
    )
    job = next(x for x in jobs if "at least one" in x.text)
    response = JobResponse.model_validate(
        {
            "findings": [
                {"kind": "req", "text": "Python", "job_evidence_id": job.id},
                {"kind": "duty", "text": "Build APIs", "job_evidence_id": job.id},
            ]
        }
    )
    result = resolve_job_requirements(response, jobs, people, 1.25)
    assert result[0]["match_state"] == "UNVERIFIED"
    assert result[0]["profile_evidence_id"] is None
    assert "alternative_requirement_unresolved" in result[0]["derived_fields"]


def test_transferable_duty_is_partial_without_direct_skill_claim():
    profile = CandidateProfile(
        experience=[ProfileEntry(text="Built data APIs and deployed backend services")],
        experience_years=1.25,
    )
    jobs, people = build_segments(
        {"description": "Required Rust. Build data APIs for services."},
        profile.model_dump(),
    )
    job = next(x for x in jobs if "Required Rust" in x.text)
    response = JobResponse.model_validate(
        {
            "findings": [
                {"kind": "req", "text": "Rust", "job_evidence_id": job.id},
                {"kind": "duty", "text": "Build data APIs", "job_evidence_id": job.id},
            ]
        }
    )
    result = resolve_job_requirements(response, jobs, people, 1.25)
    assert result[0]["match_state"] == "UNMATCHED"
    assert result[1]["match_state"] == "PARTIAL"
    assert result[1]["skill_support_level"] == "transferable"


def test_explicit_requirement_section_must_be_covered():
    profile = CandidateProfile(experience_years=1.25)
    jobs, people = build_segments(
        {"description": "Requirements\nPython\nDocker\nBuild APIs.\nNice to have\nSQL"},
        profile.model_dump(),
    )
    ids = {x.text: x.id for x in jobs if x.field.startswith("description")}
    incomplete = JobResponse.model_validate(
        {
            "findings": [
                {"kind": "req", "text": "Python", "job_evidence_id": ids["Python"]},
                {"kind": "duty", "text": "Build APIs", "job_evidence_id": ids["Build APIs."]},
            ]
        }
    )
    with pytest.raises(EvidenceError, match="missing_explicit_requirement_segment"):
        resolve_job_requirements(incomplete, jobs, people, 1.25)
    complete = JobResponse.model_validate(
        {
            "findings": [
                *[x.model_dump() for x in incomplete.findings],
                {"kind": "req", "text": "Docker", "job_evidence_id": ids["Docker"]},
            ]
        }
    )
    result = resolve_job_requirements(complete, jobs, people, 1.25)
    assert score_resolved(result, MatchingConfig())["score"] == 0
