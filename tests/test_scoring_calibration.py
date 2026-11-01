"""Module 3B.1 score-v5 calibration: comparable scores, separate evidence confidence.

All fixtures are synthetic (tests/fixtures/scoring_calibration.json). They run the
production path offline: numbered extraction, mocked Gemini role labels, Python
evidence resolution and score_resolved. No API request is made.
"""

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from job_scraper import matching
from job_scraper.candidate import CandidateProfile, profile_hash, profile_json
from job_scraper.config import MatchingConfig
from job_scraper.evidence import (
    EvidenceError,
    alternative_group,
    build_segments,
    build_skill_catalog,
    experience_scope,
)
from job_scraper.matching import analyze, ensure_schema, results, score_resolved
from job_scraper.models import Job
from job_scraper.processing import process_jobs
from job_scraper.requirements import (
    ClassifiedRequirements,
    extract_requirements,
    resolve_classifications,
)
from job_scraper.storage import Store

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "scoring_calibration.json").read_text("utf-8")
)
CASES = {case["name"]: case for case in FIXTURES["cases"]}
PROFILE = FIXTURES["profile"]

# name: (score, raw_score, applicable_weight, coverage, ceiling, interpretation, states)
EXPECTED = {
    "full_backend_match": (
        100,
        100,
        100,
        100,
        100,
        "high_score_strong_evidence",
        ["MATCHED"] * 6,
    ),
    "partial_python_job": (
        22,
        13,
        60,
        44,
        78,
        "low_score_unresolved_evidence",
        ["MATCHED", "UNVERIFIED", "UNMATCHED", "UNVERIFIED"],
    ),
    "rails_strong_professional": (
        75,
        75,
        100,
        100,
        75,
        "high_score_strong_evidence",
        ["MATCHED", "UNMATCHED", "MATCHED", "MATCHED"],
    ),
    "ai_ml_project_only": (
        73,
        55,
        75,
        100,
        73,
        "high_score_strong_evidence",
        ["PARTIAL", "MATCHED", "PARTIAL", "MATCHED"],
    ),
    "professional_python_required": (
        50,
        30,
        60,
        100,
        50,
        "low_score_confirmed_gaps",
        ["PARTIAL", "PARTIAL"],
    ),
    "unmet_experience_minimum": (
        71,
        60,
        85,
        100,
        71,
        "high_score_strong_evidence",
        ["MATCHED", "UNMATCHED", "MATCHED"],
    ),
    "unknown_pakistan_eligibility": (
        100,
        60,
        60,
        100,
        100,
        "high_score_strong_evidence",
        ["MATCHED", "MATCHED"],
    ),
    "alternative_or_group": (
        83,
        50,
        60,
        100,
        83,
        "high_score_strong_evidence",
        ["PARTIAL", "MATCHED", "MATCHED"],
    ),
    "alternative_and_plus_or": (
        50,
        30,
        60,
        67,
        83,
        "low_score_unresolved_evidence",
        ["MATCHED", "UNMATCHED", "UNVERIFIED", "UNVERIFIED", "MATCHED"],
    ),
    "missing_optional_categories": (
        100,
        60,
        60,
        100,
        100,
        "high_score_strong_evidence",
        ["MATCHED", "MATCHED"],
    ),
    "preferred_section_not_extracted": (
        80,
        60,
        75,
        80,
        100,
        "high_score_strong_evidence",
        ["MATCHED", "MATCHED"],
    ),
    "many_unverified": (
        0,
        0,
        60,
        0,
        100,
        "low_score_unresolved_evidence",
        ["UNVERIFIED"] * 5,
    ),
    "mostly_unmatched": (
        0,
        0,
        85,
        76,
        24,
        "low_score_confirmed_gaps",
        ["UNMATCHED"] * 5 + ["UNVERIFIED"],
    ),
    "mixed_professional_and_project": (
        92,
        55,
        60,
        100,
        92,
        "high_score_strong_evidence",
        ["MATCHED", "MATCHED", "MATCHED", "MATCHED", "PARTIAL"],
    ),
    "technology_specific_years": (
        35,
        30,
        85,
        85,
        50,
        "low_score_confirmed_gaps",
        ["MATCHED", "MATCHED", "UNVERIFIED", "PARTIAL", "UNMATCHED", "PARTIAL"],
    ),
    "duplicate_and_subsumed": (
        65,
        55,
        85,
        100,
        65,
        "high_score_strong_evidence",
        ["UNVERIFIED", "MATCHED", "PARTIAL", "MATCHED", "PARTIAL"],
    ),
    "unresolved_mandatory_item": (
        67,
        40,
        60,
        67,
        100,
        "high_score_limited_evidence",
        ["MATCHED", "UNVERIFIED", "MATCHED"],
    ),
}


def _assignments(case, inventory):
    return [
        {
            "requirement_id": item.id,
            "parts": [{"kind": k} for k in case["kinds"].get(item.text, ["nonreq"])],
        }
        for item in inventory
    ]


def _run(case, profile=PROFILE, config=None, order=None):
    inventory = extract_requirements(case["description"])
    assignments = _assignments(case, inventory)
    if order == "reversed":
        assignments = list(reversed(assignments))
    response = ClassifiedRequirements.model_validate({"items": assignments})
    _, segments = build_segments({"description": case["description"]}, profile)
    resolved = resolve_classifications(
        response,
        inventory,
        segments,
        profile["experience_years"],
        build_skill_catalog(segments),
    )
    return resolved, score_resolved(resolved, config or MatchingConfig(), inventory)


def _states(resolved):
    return [
        x["match_state"]
        for x in resolved
        if x["kind"] in {"req", "pref", "exp", "duty", "unresolved"}
    ]


def test_every_case_is_specified():
    assert set(EXPECTED) == set(CASES)
    CandidateProfile.model_validate(PROFILE)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_calibration_fixture(name):
    resolved, scored = _run(CASES[name])
    score, raw, applicable, coverage, ceiling, label, states = EXPECTED[name]
    confidence = scored["confidence"]
    assert _states(resolved) == states
    assert (scored["score"], scored["raw_score"], scored["applicable_weight"]) == (
        score,
        raw,
        applicable,
    )
    assert (confidence["evidence_coverage"], confidence["score_ceiling"]) == (coverage, ceiling)
    assert confidence["interpretation"] == label
    assert 0 <= scored["score"] <= confidence["score_ceiling"] <= 100
    assert confidence["confirmed_shortfall"] == 100 - ceiling
    # Weights and priorities are unchanged; only the denominator is applicable weight.
    assert {k: v["weight"] for k, v in scored["breakdown"].items()} == {
        "required_skills": 40,
        "experience": 25,
        "role_alignment": 20,
        "preferred_projects": 15,
    }


def test_absent_category_is_not_a_mismatch_or_a_reward():
    _, two = _run(CASES["missing_optional_categories"])
    assert two["breakdown"]["experience"]["status"] == "not_stated"
    assert two["breakdown"]["preferred_projects"]["status"] == "not_stated"
    assert two["breakdown"]["preferred_projects"]["points"] == 0
    assert two["raw_score"] == 60 and two["score"] == 100
    _, gap = _run(CASES["preferred_section_not_extracted"])
    # A stated preference that was not extracted keeps its weight with no credit.
    assert gap["breakdown"]["preferred_projects"]["status"] == "stated_not_extracted"
    assert gap["applicable_weight"] == 75 and gap["score"] == 80
    assert gap["confidence"]["evidence_coverage"] == 80


def test_unverified_and_unmatched_score_alike_but_differ_in_confidence():
    _, unverified = _run(CASES["many_unverified"])
    _, unmatched = _run(CASES["mostly_unmatched"])
    assert unverified["score"] == unmatched["score"] == 0
    assert unverified["confidence"]["evidence_coverage"] == 0
    assert unverified["confidence"]["score_ceiling"] == 100
    assert unmatched["confidence"]["evidence_coverage"] == 76
    assert unmatched["confidence"]["score_ceiling"] == 24
    assert unverified["confidence"]["counts"]["UNMATCHED"] == 0
    assert unmatched["confidence"]["counts"]["UNMATCHED"] == 5


def test_four_interpretations_are_distinguishable():
    labels = {
        _run(CASES[name])[1]["confidence"]["interpretation"]
        for name in (
            "full_backend_match",
            "unresolved_mandatory_item",
            "mostly_unmatched",
            "many_unverified",
        )
    }
    assert labels == {
        "high_score_strong_evidence",
        "high_score_limited_evidence",
        "low_score_confirmed_gaps",
        "low_score_unresolved_evidence",
    }


def test_alternative_group_parsing():
    source = "Strong in at least one of Python, TypeScript, Go, Rust, Java or C++"
    labels = ["Python", "TypeScript", "Go", "Rust", "Java", "C++"]
    assert alternative_group(source, labels) == (labels, True)
    assert alternative_group("Either Ruby on Rails or Java", ["Ruby on Rails", "Java"]) == (
        ["Ruby on Rails", "Java"],
        True,
    )
    assert alternative_group("Python and Docker", ["Python", "Docker"]) is None
    assert alternative_group("Python and one of Go or Rust", ["Python", "Go", "Rust"]) == (
        ["Go", "Rust"],
        True,
    )
    # Mixed AND/OR and unknown options are not structured reliably.
    assert (
        alternative_group("One of Python or Go and Kubernetes", ["Python", "Go", "Kubernetes"])[1]
        is False
    )
    assert alternative_group("At least one of Python, Elixir or Go", ["Python", "Go"])[1] is False
    assert alternative_group("one of: Python. Docker required", ["Python", "Docker"]) == (
        ["Python"],
        False,
    )


def test_alternative_group_scores_once_and_keeps_option_states():
    resolved, scored = _run(CASES["alternative_or_group"])
    group = resolved[0]
    assert group["text"] == "one of: Python | TypeScript | Go | Java"
    assert [x["match_state"] for x in group["alternative_options"]] == [
        "PARTIAL",
        "UNMATCHED",
        "UNMATCHED",
        "UNMATCHED",
    ]
    # "Strong in" asks for proficiency, so project-only Python earns PARTIAL.
    assert "project_evidence_below_proficiency_qualifier" in group["derived_fields"]
    assert scored["breakdown"]["required_skills"]["total"] == 2


def test_experience_scope_distinguishes_total_and_technology_years():
    assert experience_scope("2+ years of professional software engineering") == "total"
    assert experience_scope("3+ years of software engineering experience") == "total"
    assert experience_scope("1+ year of experience with Python") == "technology"
    assert experience_scope("3+ years of Python experience") == "technology"
    assert experience_scope("Python: 3+ years") == "technology"
    assert experience_scope("Proficient in Python. 2+ years of engineering") == "total"


def test_job22_style_experience_stays_unmatched_against_approved_years():
    resolved, _ = _run(CASES["rails_strong_professional"])
    experience = [x for x in resolved if x["kind"] == "exp"]
    assert experience[0]["text"] == "2+ years" and experience[0]["match_state"] == "UNMATCHED"
    assert PROFILE["experience_years"] == 1.25


def test_project_credit_respects_professional_and_proficiency_wording():
    resolved, _ = _run(CASES["mixed_professional_and_project"])
    python = next(x for x in resolved if x["text"] == "Python")
    assert python["match_state"] == "MATCHED"
    assert python["skill_support_level"] == "project"
    resolved, _ = _run(CASES["professional_python_required"])
    python = next(x for x in resolved if x["text"] == "Python")
    assert python["match_state"] == "PARTIAL"
    assert "project_evidence_below_professional_qualifier" in python["derived_fields"]


def test_listed_only_skills_stay_unverified():
    resolved, _ = _run(CASES["many_unverified"])
    listed = [x for x in resolved if x["skill_support_level"] == "listed"]
    assert [x["text"] for x in listed] == ["Docker", "Git", "Linux"]
    assert {x["match_state"] for x in listed} == {"UNVERIFIED"}


def test_duplicates_and_subsumed_experience_are_reported_not_double_counted():
    _, scored = _run(CASES["duplicate_and_subsumed"])
    assert [(x["text"], x["reason"]) for x in scored["excluded_findings"]] == [
        ("We need experienced engineers", "subsumed_by_numeric_experience_minimum"),
        ("Python", "duplicate_skill_requirement"),
    ]
    assert scored["breakdown"]["required_skills"]["total"] == 1
    assert scored["breakdown"]["required_skills"]["partial"] == 1


def test_scores_are_deterministic_and_order_independent():
    for case in CASES.values():
        first = _run(case)[1]
        assert _run(case)[1] == first
        reordered = _run(case, order="reversed")[1]
        assert (reordered["score"], reordered["confidence"]) == (
            first["score"],
            first["confidence"],
        )


def _items(*pairs):
    return [
        {
            "kind": kind,
            "text": f"{kind}-{index}",
            "match_state": state,
            "job_evidence_id": f"R{index:03d}",
            "derived_fields": [],
        }
        for index, (kind, state) in enumerate(pairs, 1)
    ]


def test_bounds_rounding_and_configured_weights():
    best = _items(("req", "MATCHED"), ("duty", "MATCHED"), ("exp", "MATCHED"))
    worst = _items(("req", "UNMATCHED"), ("duty", "UNMATCHED"))
    assert score_resolved(best, MatchingConfig())["score"] == 100
    assert score_resolved(worst, MatchingConfig())["score"] == 0
    # 40 * 1/2 + 20 * 1/2 = 30 of 60 applicable points.
    half = _items(("req", "MATCHED"), ("req", "UNMATCHED"), ("duty", "PARTIAL"))
    assert score_resolved(half, MatchingConfig())["score"] == 50
    weights = {
        "required_skills": 41,
        "experience": 39,
        "role_alignment": 20,
        "preferred_projects": 0,
    }
    odd = _items(("req", "MATCHED"), ("req", "PARTIAL"), ("duty", "UNMATCHED"), ("duty", "PARTIAL"))
    # 41 * 0.75 + 20 * 0.25 = 35.75 of 61 points = 58.606... -> 59.
    scored = score_resolved(odd, MatchingConfig(weights=weights))
    assert Decimal(str(scored["breakdown"]["required_skills"]["points"])) == Decimal("30.75")
    assert scored["score"] == 59 and scored["applicable_weight"] == 61
    zero = {"required_skills": 0, "experience": 50, "role_alignment": 0, "preferred_projects": 50}
    with pytest.raises(EvidenceError, match="no_applicable_weight"):
        score_resolved(worst, MatchingConfig(weights=zero))


def test_incomplete_classifications_still_fail_safely():
    case = CASES["full_backend_match"]
    inventory = extract_requirements(case["description"])
    assignments = _assignments(case, inventory)[:-1]
    _, segments = build_segments({"description": case["description"]}, PROFILE)
    with pytest.raises(EvidenceError, match="missing_requirement_id"):
        resolve_classifications(
            ClassifiedRequirements.model_validate({"items": assignments}),
            inventory,
            segments,
            1.25,
        )
    no_duty = {**case, "kinds": {**case["kinds"], "Build Rails APIs with PostgreSQL": ["req"]}}
    with pytest.raises(EvidenceError, match="missing_responsibilities"):
        _run(no_duty)


def test_requirements_heading_followed_by_responsibilities_heading_is_valid():
    resolved, _ = _run(CASES["missing_optional_categories"])
    assert [x["kind"] for x in resolved] == ["req", "duty"]


def _store_with_job(store, case):
    job = Job(
        source="remotive",
        source_id=case["name"],
        company="Synthetic Co",
        title="Software Engineer",
        description=case["description"],
        workplace_type="remote",
        source_url=f"https://example.com/jobs/{case['name']}",
        attribution="Synthetic",
        attribution_url="https://example.com",
    )
    store.upsert(job)
    process_jobs(store)
    ensure_schema(store)
    profile = CandidateProfile.model_validate(PROFILE)
    with store.db:
        store.db.execute(
            "INSERT INTO candidate_profiles(created_at,approved,profile_hash,profile) "
            "VALUES (?,?,?,?)",
            ("2026-11-02", 1, profile_hash(profile), profile_json(profile)),
        )


def _factory(case):
    def respond(**kwargs):
        sent = json.loads(kwargs["contents"])["requirements"]
        items = [
            {
                "requirement_id": x["id"],
                "parts": [{"kind": k} for k in case["kinds"].get(x["text"], ["nonreq"])],
            }
            for x in sent
        ]
        return MagicMock(text=json.dumps({"items": items}), usage_metadata=None)

    factory = MagicMock()
    factory.return_value.__enter__.return_value.models.generate_content.side_effect = respond
    return factory


def test_high_score_never_changes_unverified_eligibility(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    case = CASES["unknown_pakistan_eligibility"]
    with Store(":memory:") as store:
        _store_with_job(store, case)
        before = store.db.execute("SELECT result FROM job_classifications").fetchall()
        config = MatchingConfig(retries=0, max_requests_per_run=1)
        assert analyze(store, config, live=True, client_factory=_factory(case))["saved"] == 1
        record = results(store, config)[0]
        assert record["score"] == 100
        assert record["eligibility_status"] == "UNVERIFIED"
        assert record["confidence"]["interpretation"] == "high_score_strong_evidence"
        assert store.db.execute("SELECT result FROM job_classifications").fetchall() == before
        assert json.loads(before[0][0])["status"] == "UNVERIFIED"


def test_rule_change_marks_old_assessment_stale_and_keeps_history(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    case = CASES["rails_strong_professional"]
    with Store(":memory:") as store:
        _store_with_job(store, case)
        config = MatchingConfig(retries=0, max_requests_per_run=1)
        factory = _factory(case)
        assert analyze(store, config, live=True, client_factory=factory)["saved"] == 1
        original = store.db.execute("SELECT * FROM ai_assessments").fetchone()
        again = analyze(store, config, live=True, client_factory=factory)
        assert again["cached"] == 1 and again["requests"] == 0
        assert results(store, config)[0]["stale"] is False
        monkeypatch.setattr(matching, "SCORING_VERSION", "score-v-future")
        assert results(store, config)[0]["stale"] is True
        assert analyze(store, config, live=True, client_factory=factory)["saved"] == 1
        rows = store.db.execute("SELECT * FROM ai_assessments ORDER BY id").fetchall()
        assert len(rows) == 2 and tuple(rows[0]) == tuple(original)
        listed = results(store, config)
        assert [x["stale"] for x in listed] == [False, True]
        assert listed[1]["id"] == original["id"]
