"""Module 3 changes from the November 2026 refresh audit (requirements-v4 / evidence-v10)."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from test_storage import job

from job_scraper import matching
from job_scraper.candidate import CandidateProfile, ProfileEntry, profile_hash, profile_json
from job_scraper.config import MatchingConfig
from job_scraper.evidence import EvidenceError, build_segments, build_skill_catalog
from job_scraper.experience import compare, parse_requirement
from job_scraper.matching import analyze, ensure_schema, results
from job_scraper.processing import process_jobs
from job_scraper.requirements import (
    VERSION,
    ClassifiedRequirements,
    ambiguous_location_policy,
    extract_requirements,
    normalize_kinds,
    resolve_classifications,
)
from job_scraper.storage import Store

GITLAB = (Path(__file__).parent / "fixtures" / "job2932_description.txt").read_text(
    encoding="utf-8"
)
R051 = (
    "GitLab hires new team members in countries around the world. All of our roles are remote, "
    "however some roles may carry specific location-based eligibility requirements. Our Talent "
    "Acquisition team can help answer any questions about location after starting the "
    "recruiting process."
)


def test_versions():
    assert VERSION == "requirements-v4"
    assert (matching.SCORING_VERSION, matching.EVIDENCE_VERSION) == ("score-v6", "evidence-v12")
    assert (matching.PROMPT_VERSION, matching.ANALYSIS_SCHEMA_VERSION) == (
        "match-v9",
        "analysis-v7",
    )


# --- engineering relevance -------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Account Executive, Portworx - Strategic Accounts - Telco (EMEA)",
        "FP&A Lead",
        "Senior Veterinary Surgeon - Farm/Mixed",
        "Pflichtpraktikum im Bereich Grafikdesign (m/w/d)",
        "Product Manager",
        "Design Mechanical Engineer",
        "Senior Hardware Engineer - Drone Port (m/f/d)",
        "Process Engineer Halbleiter 100% (a)",
        "Instruments Manufacturing Engineer",
        "Sales Engineer",
    ],
)
def test_non_engineering_and_non_software_titles_are_not_relevant_even_if_the_text_says_build(
    title,
):
    posting = {"title": title, "description": "Build services and applications for customers."}
    assert matching._relevant(posting, {}) is False


@pytest.mark.parametrize(
    "title",
    [
        "Backend Engineer, Source Code: Repository Services (Ruby)",
        "Fullstack Engineer - AgenticOps (100% Remote)",
        "Founding Engineering Team Lead (HandsOn)",
        "Member of Technical Staff",
        "Junior Software Engineer",
        "Machine Learning Engineer",
        "AI Platform Developer",
    ],
)
def test_software_engineering_titles_remain_relevant(title):
    assert matching._relevant({"title": title, "description": ""}, {}) is True


def test_module2_categories_keep_a_role_relevant():
    assert matching._relevant({"title": "Technologist"}, {"categories": ["backend"]}) is True


# --- deterministic normalization (R051 and footers) ---------------------------------------


def one(text):
    return extract_requirements(text)[0]


def test_ambiguous_location_policy_is_recognised_only_when_nothing_mandatory_is_in_the_item():
    assert ambiguous_location_policy(R051)
    for text in (
        R051 + " Candidates must reside in the United States.",
        R051 + " Applicants must be authorized to work in the UK.",
        "Some roles may carry location requirements. We only hire candidates located in Germany.",
        "This role is only available to candidates located in Germany.",
        "All of our roles are remote.",
        "We hire in many countries.",
    ):
        assert not ambiguous_location_policy(text), text


def test_r051_nonreq_becomes_unresolved_and_everything_else_is_unchanged():
    note = normalize_kinds(one(R051), ["nonreq"])
    assert note[0] == ["unresolved"] and note[1] == "ambiguous_location_policy"
    assert normalize_kinds(one(R051), ["eligibility"]) is None
    assert normalize_kinds(one(R051), ["unresolved"]) is None
    assert normalize_kinds(one(R051), ["duty"]) is None
    assert normalize_kinds(one("Candidates must reside in the United States."), ["nonreq"]) is None


def test_job_board_footer_is_not_a_hiring_condition():
    footer = "Jobs in United Kingdom\non Arbeitnow"
    note = normalize_kinds(one(footer), ["eligibility"])
    assert note and note[0] == ["nonreq"] and note[1] == "source_site_footer"
    assert normalize_kinds(one("English Speaking Jobs in Germany on Arbeitnow"), ["eligibility"])
    # an employer statement is never a footer
    assert normalize_kinds(one("Jobs in the United Kingdom only."), ["eligibility"]) is None
    assert (
        normalize_kinds(
            one("This role is open to Jobs in United Kingdom residents."), ["eligibility"]
        )
        is None
    )


def classify(inventory, **kinds):
    def default(entry):
        if entry.heading:
            return "nonreq"
        return "req" if any(not m.negated for m in entry.technologies) else "duty"

    return ClassifiedRequirements.model_validate(
        {
            "items": [
                {"requirement_id": x.id, "parts": [{"kind": kinds.get(x.id, default(x))}]}
                for x in inventory
            ]
        }
    )


def resolve(inventory, response, profile=None, notes=None):
    profile = profile or CandidateProfile(skills=["Ruby"], experience_months=15)
    _, segments = build_segments({"description": ""}, profile.model_dump())
    return resolve_classifications(
        response,
        inventory,
        segments,
        None,
        build_skill_catalog(segments),
        experience_months=profile.experience_months,
        notes=notes,
    )


def test_mandatory_condition_mixed_with_the_ambiguous_policy_still_fails():
    inventory = extract_requirements(
        "Experience with Ruby.\n"
        + R051
        + " Candidates must reside in the United States.\nMaintain services."
    )
    with pytest.raises(EvidenceError) as caught:
        resolve(inventory, classify(inventory, R002="nonreq"))
    assert (caught.value.reason, caught.value.requirement_id) == ("restriction_discarded", "R002")


def test_footer_eligibility_label_is_normalized_and_traced():
    inventory = extract_requirements(
        "Experience with Ruby.\nMaintain services.\nJobs in United Kingdom\non Arbeitnow"
    )
    notes = []
    findings = resolve(inventory, classify(inventory, R003="eligibility"), notes=notes)
    assert [n["rule"] for n in notes] == ["source_site_footer"]
    assert all(f["job_evidence_id"] != "R003" or f["kind"] != "eligibility" for f in findings)


def test_saved_assessment_keeps_the_original_label_and_records_the_normalization(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-only")
    from test_compensation_restriction import gitlab_response

    inventory = extract_requirements(GITLAB)
    response = gitlab_response(inventory, R051="nonreq")
    profile = CandidateProfile(
        experience=[ProfileEntry(text="Built Ruby on Rails APIs for a product")],
        skills=["Ruby", "Git"],
        experience_months=15,
    )
    posting = job(
        source="arbeitnow",
        board="",
        source_id="gitlab-fixture",
        company="gitlab",
        title="Backend Engineer, Source Code: Repository Services (Ruby)",
        description=GITLAB,
        workplace_type="remote",
        source_url="https://example.com/gitlab-fixture",
    )
    with Store(":memory:") as store:
        store.upsert(posting)
        process_jobs(store)
        ensure_schema(store)
        with store.db:
            store.db.execute(
                "INSERT INTO candidate_profiles(created_at,approved,profile_hash,profile) VALUES (?,?,?,?)",
                ("2026-11-04", 1, profile_hash(profile), profile_json(profile)),
            )
        factory = MagicMock()
        method = factory.return_value.__enter__.return_value.models.generate_content
        method.return_value = MagicMock(text=response.model_dump_json(), usage_metadata=None)
        config = MatchingConfig(retries=0, max_requests_per_run=1)
        outcome = analyze(store, config, live=True, job_id=1, client_factory=factory)
        assert outcome["saved"] == 1 and not outcome["failures"]
        saved = results(store, config, job_id=1)[0]["result"]
    original = {c["requirement_id"]: c for c in saved["requirement_classifications"]}
    assert [p["kind"] for p in original["R051"]["parts"]] == ["nonreq"]  # the model's own label
    assert [n["rule"] for n in saved["classification_normalizations"]] == [
        "ambiguous_location_policy"
    ]
    kinds = {f["kind"] for f in saved["referenced_findings"] if f["job_evidence_id"] == "R051"}
    assert kinds == {"unresolved"}


# --- experience parity with Module 2 ---------------------------------------------------------


@pytest.mark.parametrize(
    "text,months,kind",
    [
        ("three years of experience", 36, "minimum"),
        ("Five years of hands-on experience in security engineering", 60, "minimum"),
        ("At least five years of experience", 60, "minimum"),
        ("at least two years of experience", 24, "minimum"),
        ("Four to seven years of experience", 48, "range"),
        ("[3+] years as a Backend Engineer with experience", 36, "minimum"),
        ("(5) years of software experience", 60, "minimum"),
        ("Five (5) years of software development experience", 60, "minimum"),
        ("Seven or more years of experience", 84, "minimum"),
        ("4 to 5 plus years of professional experience", 48, "range"),
        ("3+ years of experience", 36, "minimum"),
        ("2–3 years of experience", 24, "range"),
        ("up to five years of professional experience", 60, "maximum"),
        ("five years of experience preferred", 60, "preferred"),
        ("ideally three years of software experience", 36, "preferred"),
    ],
)
def test_written_numbers_and_brackets_are_read_like_module_2(text, months, kind):
    found = parse_requirement(text)
    assert (found.minimum_months, found.kind) == (months, kind)


@pytest.mark.parametrize(
    "text",
    [
        "three years in a sales cycle with experience",
        "Five years ago a strong engineer was measured by what they could build.",
        "a one year contract with experience in software",
        "the next five years of software development",
        "within the first six months of experience",
        "after three years of professional experience you move into a lead role",
        "10 years old product with engineering experience",
    ],
)
def test_unrelated_durations_are_not_experience_requirements(text):
    assert parse_requirement(text) is None


def test_gap_for_a_written_requirement_uses_the_approved_months_unchanged():
    view = compare(parse_requirement("Five years of professional experience"), 15)
    assert (view["gap_months"], view["met"], view["approved_months"]) == (45, False, 15)
    assert compare(parse_requirement("one year of experience"), 15)["met"] is True


def test_written_minimum_must_be_labelled_exp_by_the_model():
    inventory = extract_requirements(
        "Five years of professional experience in Python\nPython\nBuild Python APIs"
    )
    with pytest.raises(EvidenceError) as caught:
        resolve(inventory, classify(inventory, R001="req"))
    assert caught.value.reason == "experience_minimum_misclassified"
    # a time-frame sentence is not an experience item, so labelling it nonreq is accepted
    inventory = extract_requirements(
        "Python\nBuild Python APIs\nFive years ago a strong engineer was measured by output."
    )
    resolve(inventory, classify(inventory, R002="duty", R003="nonreq"))


# --- alternatives versus cumulative requirements ------------------------------------------


PROFILE = CandidateProfile(
    experience=[
        ProfileEntry(text="Built React front-end features using React and Vite in production")
    ],
    skills=["React", "Python", "Docker"],
    experience_months=15,
)


def findings_for(requirement):
    inventory = extract_requirements(
        f"Requirements\n{requirement}\nResponsibilities\nBuild web apps"
    )
    response = classify(inventory, R002="req", R004="duty")
    return [
        f
        for f in resolve(inventory, response, PROFILE)
        if f["job_evidence_id"] == "R002" and f["kind"] == "req"
    ]


def test_react_or_typescript_is_one_alternative_requirement_satisfied_by_react():
    found = findings_for("Professional experience with React or TypeScript")
    assert len(found) == 1
    assert found[0]["match_state"] == "MATCHED"
    assert {o["text"] for o in found[0]["alternative_options"]} == {"React", "TypeScript"}


def test_react_and_typescript_stay_cumulative():
    found = findings_for("Professional experience with React and TypeScript")
    states = {f["text"]: f["match_state"] for f in found}
    assert states["React"] == "MATCHED" and states["TypeScript"] == "UNMATCHED"
    assert all("alternative_options" not in f for f in found)


def test_slash_lists_are_not_assumed_to_mean_either_or():
    found = findings_for("Strong JavaScript/TypeScript skills")
    assert len(found) == 1
    assert found[0]["match_state"] == "UNVERIFIED"  # unresolved, not a confirmed gap
    assert "alternative_requirement_unresolved" in found[0]["derived_fields"]
    assert all(o["match_state"] == "UNVERIFIED" for o in found[0]["alternative_options"])


def test_a_slash_list_does_not_swallow_a_separate_and_requirement():
    found = findings_for("Strong JavaScript/TypeScript and Node.js skills")
    texts = [f["text"] for f in found]
    assert any(t.startswith("one of") for t in texts)
    node = next(f for f in found if f["text"] == "Node.js")
    assert node["match_state"] == "UNMATCHED"  # a separate, cumulative requirement


def test_comma_lists_are_requirements_not_alternatives():
    found = findings_for("Experience with Python, Docker, Git")
    assert {f["text"] for f in found} == {"Python", "Docker", "Git"}
    assert all("alternative_options" not in f for f in found)


def test_explicit_one_of_wording_is_unchanged():
    found = findings_for("At least one of Python, Ruby or Go")
    assert len(found) == 1 and "alternative_options" in found[0]


def test_normalization_notes_are_plain_data():
    notes = []
    inventory = extract_requirements("Experience with Ruby.\n" + R051 + "\nMaintain services.")
    resolve(inventory, classify(inventory, R002="nonreq"), notes=notes)
    assert json.loads(json.dumps(notes))[0]["original"] == ["nonreq"]
