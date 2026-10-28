"""Offline regressions for source references and unsupported model conclusions."""

import hashlib

import pytest

from job_scraper.evidence import (
    EvidenceError,
    ReferencedResponse,
    build_project_evidence,
    build_segments,
    build_skill_catalog,
    canonical_skill,
    phrase_in,
    resolve,
)


def _sources(description="Required Python. 2+ years of professional software engineering."):
    profile = {
        "experience": [{"text": "Built Python APIs", "dates": ["2025", "Present"]}],
        "skills": ["Python"],
        "technologies": [],
        "projects": [],
        "education": [],
        "certifications": [],
        "experience_years": 1.25,
    }
    job = {"title": "Software Engineer", "company": "Example", "description": description}
    return build_segments(job, profile)


def _finding(kind, text, job, profile=None, matched=False):
    return {
        "kind": kind,
        "text": text,
        "job_evidence_id": job.id,
        "profile_evidence_id": profile.id if profile else None,
        "candidate_skill_id": (
            "S-" + hashlib.sha256(canonical_skill(text).encode()).hexdigest()[:12]
            if kind in {"req", "pref"} and matched
            else None
        ),
        "matched": matched,
    }


def _resolve(items, jobs, people):
    return resolve(ReferencedResponse(findings=items), jobs, people, 1.25)


def test_stable_ids_and_whitespace_normalization():
    jobs, people = _sources("Required\nPython. 2+ years of professional software engineering.")
    assert (jobs, people) == _sources(
        "Required\nPython. 2+ years of professional software engineering."
    )
    assert jobs != _sources("Required Ruby. 2+ years of professional software engineering.")[0]
    assert phrase_in("Required\nPython", "Required Python")
    assert not phrase_in("Required\nPython", "Required Ruby")
    assert not phrase_in("Django", "Go")


def test_valid_multiline_and_unmet_experience():
    jobs, people = _sources("Required\nPython.\n2+ years of professional software engineering.")
    description = [x for x in jobs if x.field.startswith("description")]
    skill = next(x for x in people if x.field.startswith("experience"))
    items = [
        _finding("req", "Python", description[1], skill, True),
        _finding("exp", "2+ years", description[2]),
    ]
    out = _resolve(items, jobs, people)
    assert out[0]["profile_evidence_id"] == skill.id
    assert out[1]["matched"] is False
    assert out[1]["job_excerpt"] == description[2].text


@pytest.mark.parametrize(
    "change,reason",
    [
        ("job", "unknown_id"),
        ("profile", "unknown_id"),
        ("quote", "claim_not_in_job_source"),
        ("misleading", "wrong_skill_evidence"),
    ],
)
def test_rejects_invented_or_misleading_evidence(change, reason):
    jobs, people = _sources()
    description = next(x for x in jobs if x.field.startswith("description"))
    skill = next(x for x in people if x.field.startswith("experience"))
    years = next(x for x in people if x.field.startswith("experience_years"))
    item = _finding("req", "Python", description, skill, True)
    if change == "job":
        item["job_evidence_id"] = "J-invented"
    elif change == "profile":
        item["profile_evidence_id"] = "P-invented"
    elif change == "quote":
        item["text"] = "Rust"
    elif change == "misleading":
        item["profile_evidence_id"] = years.id
    with pytest.raises(EvidenceError) as caught:
        _resolve([item], jobs, people)
    assert caught.value.reason == reason
    assert caught.value.field.startswith("findings[0]")


def test_numeric_experience_ignores_model_match_flag_and_uses_approved_years():
    jobs, people = _sources()
    description = next(x for x in jobs if x.field.startswith("description"))
    years = next(x for x in people if x.field.startswith("experience_years"))
    output = _resolve([_finding("exp", "2+ years", description, years, True)], jobs, people)
    assert output[0]["match_state"] == "UNMATCHED"
    assert output[0]["matched"] is False
    assert "ignored_legacy_matched_flag" in output[0]["derived_fields"]


def test_experience_requirement_cannot_be_omitted():
    jobs, people = _sources()
    description = next(x for x in jobs if x.field.startswith("description"))
    skill = next(x for x in people if x.field.startswith("experience"))
    with pytest.raises(EvidenceError, match="missing_experience_requirement"):
        _resolve([_finding("req", "Python", description, skill, True)], jobs, people)


def test_missing_claim_cannot_contradict_profile():
    jobs, people = _sources()
    description = next(x for x in jobs if x.field.startswith("description"))
    items = [
        _finding("exp", "2+ years", description),
        _finding("missing", "Python", description),
    ]
    with pytest.raises(EvidenceError, match="requirement_present_in_profile"):
        _resolve(items, jobs, people)


def test_citation_does_not_make_unrelated_duty_match():
    jobs, people = _sources("Deploy Kubernetes clusters. 2+ years of engineering experience.")
    description = next(x for x in jobs if x.field.startswith("description"))
    skill = next(x for x in people if x.field.startswith("experience"))
    with pytest.raises(EvidenceError, match="no_supported_alignment"):
        _resolve(
            [_finding("duty", "Deploy Kubernetes clusters", description, skill, True)], jobs, people
        )


def test_negated_profile_skill_does_not_establish_match():
    jobs, people = _sources()
    description = next(x for x in jobs if x.field.startswith("description"))
    skill = next(x for x in people if x.field.startswith("skills"))
    negated = type(skill)(id=skill.id, field=skill.field, text="No Python")
    people = [negated if x.id == skill.id else x for x in people]
    with pytest.raises(EvidenceError, match="wrong_skill_evidence"):
        _resolve([_finding("req", "Python", description, negated, True)], jobs, people)


def test_catalog_alias_and_support_levels_are_stable():
    profile = {
        "experience": [{"text": "Built Rails APIs", "dates": ["2025"]}],
        "projects": [{"text": "Created Python APIs", "dates": ["2024"]}],
        "skills": ["Ruby on Rails", "Python", "Docker"],
        "technologies": [],
        "education": [],
        "certifications": [],
        "experience_years": 1.25,
    }
    jobs, people = build_segments(
        {"title": "Engineer", "description": "Required Rails. Preferred Python. Required Docker."},
        profile,
    )
    catalog = build_skill_catalog(people)
    assert catalog == build_skill_catalog(people)
    rails = next(item for item in catalog if item.name == "ruby on rails")
    python = next(item for item in catalog if item.name == "python")
    docker = next(item for item in catalog if item.name == "docker")
    assert {x.level for x in rails.supports} == {"listed", "professional"}
    assert {x.level for x in python.supports} == {"listed", "project"}
    assert {x.level for x in docker.supports} == {"listed"}
    description = next(x for x in jobs if x.field.startswith("description"))
    rails_source = next(x for x in people if x.field.startswith("experience"))
    python_source = next(x for x in people if x.field.startswith("projects"))
    output = _resolve(
        [
            _finding("req", "Rails", description, rails_source, True),
            _finding("pref", "Python", description, python_source, True),
            _finding("req", "Docker", description),
        ],
        jobs,
        people,
    )
    assert [x["skill_support_level"] for x in output] == ["professional", "project", None]
    assert output[0]["candidate_skill_id"] == rails.id
    assert output[1]["candidate_skill_id"] == python.id


def test_listed_skill_does_not_prove_professional_skill():
    jobs, people = _sources("Required Docker. 2+ years of engineering.")
    listed = type(people[0])(id="P-" + "a" * 12, field="skills[1][0]", text="Docker")
    people.append(listed)
    job = next(x for x in jobs if x.field.startswith("description"))
    result = _resolve(
        [
            _finding("req", "Docker", job, listed, True),
            _finding("exp", "2+ years", job),
        ],
        jobs,
        people,
    )
    assert result[0]["match_state"] == "UNVERIFIED"
    assert result[0]["matched"] is False


def test_negated_experience_does_not_upgrade_listed_skill():
    jobs, people = build_segments(
        {"title": "Engineer", "description": "Required Docker."},
        {
            "experience": [{"text": "Did not use Docker; built Python APIs", "dates": []}],
            "skills": ["Docker"],
            "technologies": [],
            "projects": [],
            "education": [],
            "certifications": [],
            "experience_years": 1.25,
        },
    )
    docker = next(x for x in build_skill_catalog(people) if x.name == "docker")
    assert {x.level for x in docker.supports} == {"listed"}
    job = next(x for x in jobs if x.field.startswith("description"))
    experience = next(x for x in people if x.field.startswith("experience"))
    with pytest.raises(EvidenceError, match="wrong_skill_evidence"):
        _resolve([_finding("req", "Docker", job, experience, True)], jobs, people)


def test_unsupported_skill_and_misleading_catalog_citation():
    jobs, people = _sources("Required Kubernetes. Required Python. 2+ years of engineering.")
    description = next(x for x in jobs if x.field.startswith("description"))
    professional = next(x for x in people if x.field.startswith("experience"))
    with pytest.raises(EvidenceError, match="unknown_skill_id"):
        _resolve([_finding("req", "Kubernetes", description, professional, True)], jobs, people)
    years = next(x for x in people if x.field.startswith("experience_years"))
    with pytest.raises(EvidenceError, match="wrong_skill_evidence"):
        _resolve([_finding("req", "Python", description, years, True)], jobs, people)


def test_transferable_experience_is_labeled_inference():
    jobs, people = _sources("Build Python APIs. 2+ years of engineering.")
    description = next(x for x in jobs if x.field.startswith("description"))
    professional = next(x for x in people if x.field.startswith("experience"))
    output = _resolve(
        [
            _finding("exp", "2+ years", description),
            _finding("duty", "Build Python APIs", description, professional, True),
            _finding("transfer", "Build Python APIs", description, professional, True),
        ],
        jobs,
        people,
    )
    assert output[-1]["interpretation_status"] == "model_inference"


def test_deepfake_project_groups_python_pytorch_and_hands_on_work():
    deepfake = (
        "DeepFake Detection — Final Year Thesis | Python, PyTorch, TensorFlow, OpenCV. "
        "Curated 8,000+ frame dataset (FaceForensics++) with novel pre-processing "
        "(SMOTE, AutoEncoder denoising, ZeroDCE lighting). Implemented ResNet50 + LSTM "
        "artifact extractor; achieved ROC-AUC ≈ 0.88 and precision 1.00. "
        "Deployed on GPU-accelerated pipeline via Streamlit & OpenCV."
    )
    profile = {
        "experience": [],
        "projects": [{"text": deepfake, "dates": []}],
        "skills": ["Python", "PyTorch", "Docker"],
        "technologies": [],
        "education": [],
        "certifications": [],
        "experience_years": 1.25,
    }
    jobs, people = build_segments(
        {
            "title": "Engineer",
            "description": "Strong in Python. 2+ years of professional engineering.",
        },
        profile,
    )
    project = build_project_evidence(people)[0]
    assert project.title.startswith("DeepFake Detection")
    assert project.technologies == ("Python", "PyTorch", "TensorFlow", "OpenCV")
    assert "Implemented ResNet50" in project.activities
    assert project.activity_evidence_ids == ("P-d0d0fdea0523",)
    catalog = build_skill_catalog(people)
    for name in ("python", "pytorch"):
        support = next(
            x
            for skill in catalog
            if skill.name == name
            for x in skill.supports
            if x.evidence_id == "P-d0d0fdea0523"
        )
        assert support.level == "project"
        assert support.project_id == project.id
        assert support.activity_evidence_ids == project.activity_evidence_ids
    assert {x.level for x in next(s for s in catalog if s.name == "docker").supports} == {"listed"}
    description = next(x for x in jobs if x.field.startswith("description"))
    source = next(x for x in people if x.id == "P-d0d0fdea0523")
    findings = [
        _finding("req", "Python", description, source, True),
        _finding("exp", "2+ years", description),
    ]
    output = _resolve(findings, jobs, people)
    assert output[0]["skill_support_level"] == "project"
    assert output[1]["matched"] is False


def test_project_group_does_not_connect_unrelated_entries_or_tasks():
    profile = {
        "experience": [],
        "projects": [
            {"text": "Research | Python, Docker. Wrote a report.", "dates": []},
            {"text": "Web app | React. Built a React UI.", "dates": []},
        ],
        "skills": ["Python", "Docker", "React"],
        "technologies": [],
        "education": [],
        "certifications": [],
        "experience_years": 1.25,
    }
    jobs, people = build_segments(
        {"title": "Engineer", "description": "Required Python. Required React."}, profile
    )
    catalog = build_skill_catalog(people)
    first = next(x for x in people if x.field.startswith("projects[0]"))
    second = next(x for x in people if x.field.startswith("projects[1]"))
    assert next(x for x in catalog if x.name == "python").supports[0].level == "listed"
    assert next(x for x in catalog if x.name == "docker").supports[0].level == "listed"
    assert (
        next(
            x
            for x in next(s for s in catalog if s.name == "react").supports
            if x.evidence_id == second.id
        ).level
        == "project"
    )
    description = next(x for x in jobs if x.field.startswith("description"))
    result = _resolve([_finding("req", "Python", description, first, True)], jobs, people)
    assert result[0]["match_state"] == "UNVERIFIED"
    with pytest.raises(EvidenceError, match="wrong_skill_evidence"):
        _resolve([_finding("req", "Python", description, second, True)], jobs, people)


def test_unrelated_activity_in_same_project_does_not_upgrade_all_technologies():
    _, people = build_segments(
        {"title": "Engineer", "description": "Required Python and React."},
        {
            "experience": [],
            "projects": [
                {
                    "text": "Web design | Python, React, Docker. Built a React UI component.",
                    "dates": [],
                }
            ],
            "skills": ["Python", "React", "Docker"],
            "technologies": [],
            "education": [],
            "certifications": [],
            "experience_years": 1.25,
        },
    )
    catalog = build_skill_catalog(people)
    project_id = next(x.id for x in people if x.field.startswith("projects[0].text"))
    levels = {
        name: next(
            ref.level
            for skill in catalog
            if skill.name == name
            for ref in skill.supports
            if ref.evidence_id == project_id
        )
        for name in ("python", "react", "docker")
    }
    assert levels == {"python": "listed", "react": "project", "docker": "listed"}
