"""Experience as whole months: conversion, display, comparison and compatibility.

The approved total is a snapshot supplied by the user. Nothing here reads a calendar,
adds up employment dates or changes a stored profile; legacy decimal years keep working.
"""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from test_offline_rescore import CONFIG as RESCORE_CONFIG
from test_offline_rescore import _factory, _historic, _setup
from test_offline_rescore import _job as _job22
from test_ranking import M, U, _assess, _items, _job, _seed
from test_requirement_inventory import JOB22, _classifications

from job_scraper import matching, ranking
from job_scraper.candidate import (
    CandidateProfile,
    ProfileEntry,
    experience_summary,
    profile_hash,
    profile_json,
)
from job_scraper.config import MatchingConfig
from job_scraper.evidence import (
    EvidenceError,
    JobResponse,
    build_segments,
    experience_scope,
    resolve_job_requirements,
)
from job_scraper.experience import (
    compare,
    format_months,
    minimum_months,
    months_from_years,
    parse_requirement,
)
from job_scraper.matching import (
    analyze,
    approve_profile,
    ensure_schema,
    export_profile,
    latest_profile,
    rescore,
    results,
)
from job_scraper.processing import VERSION, classify
from job_scraper.requirements import (
    ClassifiedRequirements,
    extract_requirements,
    resolve_classifications,
)
from job_scraper.storage import Store

CONFIG = MatchingConfig()


@pytest.mark.parametrize(
    "months,text",
    [
        (0, "0 months"),
        (1, "1 month"),
        (2, "2 months"),
        (6, "6 months"),
        (11, "11 months"),
        (12, "1 year"),
        (13, "1 year, 1 month"),
        (15, "1 year, 3 months"),
        (18, "1 year, 6 months"),
        (24, "2 years"),
        (27, "2 years, 3 months"),
        (36, "3 years"),
        (38, "3 years, 2 months"),
    ],
)
def test_month_display(months, text):
    assert format_months(months) == text


def test_unknown_experience_is_never_displayed_as_zero():
    assert format_months(None) is None
    assert CandidateProfile().experience_months is None
    assert CandidateProfile().experience_display is None
    assert CandidateProfile().experience_summary()["months"] is None
    view = compare(parse_requirement("2+ years"), None)
    assert view["approved_months"] is None and view["approved_display"] is None
    assert view["gap_months"] is None and view["gap_display"] is None and view["met"] is None


@pytest.mark.parametrize(
    "years,months,exact",
    [(1.25, 15, True), (0.5, 6, True), (2.0, 24, True), (3, 36, True), (0, 0, True)]
    + [(1.3, 15, False), (1.1666666666666667, 14, False), (0.1, 1, False)],
)
def test_legacy_decimal_years_convert_exactly_or_round_down_explicitly(years, months, exact):
    assert months_from_years(years) == (months, exact)


def test_inexact_legacy_values_are_reported_never_rounded_up():
    profile = CandidateProfile(experience_years=1.3)
    summary = profile.experience_summary()
    assert profile.experience_months == 15  # 15.6 months rounds DOWN
    assert summary["exact"] is False and "rounded down" in summary["note"]
    with pytest.raises(ValueError, match="negative"):
        months_from_years(-1)


def test_months_are_canonical_and_years_are_only_legacy_input():
    legacy = CandidateProfile(experience_years=1.25)
    modern = CandidateProfile(experience_months=15)
    assert legacy.experience_months == modern.experience_months == 15
    assert legacy.experience_years == modern.experience_years == 1.25  # derived, readable
    stored = json.loads(profile_json(modern))
    assert stored["experience_months"] == 15 and "experience_years" not in stored
    assert json.loads(profile_json(legacy)) == stored  # years are converted, not stored twice
    assert CandidateProfile(experience_years=1.25, experience_months=15).experience_months == 15
    with pytest.raises(ValueError, match="disagree"):
        CandidateProfile(experience_years=1.25, experience_months=14)
    with pytest.raises(ValueError, match="disagree"):
        CandidateProfile(experience_years=1.3, experience_months=15)


def test_profile_hash_is_the_same_for_years_and_months_forms():
    legacy = {
        "schema_version": "candidate-v1",
        "experience": [{"text": "Software Engineer at Acme", "dates": ["2025", "Present"]}],
        "skills": ["Python"],
        "projects": [],
        "education": [],
        "certifications": [],
        "technologies": ["Python"],
        "experience_years": 1.25,
    }
    # The hash the application computed before months existed.
    old = hashlib.sha256(
        json.dumps(legacy, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    from_years = CandidateProfile.model_validate(legacy)
    from_months = CandidateProfile.model_validate(
        {**{k: v for k, v in legacy.items() if k != "experience_years"}, "experience_months": 15}
    )
    assert profile_hash(from_years) == profile_hash(from_months) == old
    # A different amount of experience is a different profile.
    assert profile_hash(CandidateProfile(experience_months=16)) != profile_hash(
        CandidateProfile(experience_months=15)
    )
    # Reading never changes the stored dictionary.
    frozen = copy.deepcopy(legacy)
    CandidateProfile.model_validate(legacy)
    assert legacy == frozen


def test_stored_legacy_profile_summary_is_derived_not_written():
    stored = {"schema_version": "candidate-v1", "experience_years": 1.25}
    summary = experience_summary(stored)
    assert summary == {
        "months": 15,
        "display": "1 year, 3 months",
        "source": "experience_years",
        "exact": True,
        "note": None,
    }
    assert stored == {"schema_version": "candidate-v1", "experience_years": 1.25}
    assert experience_summary({"schema_version": "wrong"})["months"] is None


def test_continued_employment_sections_are_never_added_up():
    entry = ProfileEntry(
        text="Software Engineer - Dubizzle Labs, July 2025 - Present", dates=["2025", "Present"]
    )
    continued = ProfileEntry(
        text="Software Engineer - Dubizzle Labs (continued responsibilities)",
        dates=["2025", "Present"],
    )
    one = CandidateProfile(experience=[entry], experience_months=15)
    two = CandidateProfile(experience=[entry, continued], experience_months=15)
    assert one.experience_months == two.experience_months == 15
    # Dates and entries are never summed or turned into months.
    assert CandidateProfile(experience=[entry, continued]).experience_months is None
    projects = CandidateProfile(
        projects=[ProfileEntry(text="Thesis | Python. Built a model", dates=["2024", "2025"])],
        experience_months=15,
    )
    assert projects.experience_months == 15


@pytest.mark.parametrize(
    "text,months,kind",
    [
        ("6 months of experience", 6, "minimum"),
        ("1 year", 12, "minimum"),
        ("1.5 years of experience", 18, "minimum"),
        ("2+ years of professional software engineering", 24, "minimum"),
        ("3 years", 36, "minimum"),
        ("18 months professional experience", 18, "minimum"),
        ("1 year and 6 months of experience", 18, "minimum"),
        ("2 years and 6 months of experience", 30, "minimum"),
        ("2-3 years experience", 24, "range"),
        ("Ideally 5 years of Rails", 60, "preferred"),
        ("Nice to have: 3 years of Go", 36, "preferred"),
        ("5 years or equivalent", 60, "ambiguous"),
        ("up to 2 years of experience", 24, "maximum"),
    ],
)
def test_requirement_conversion_and_kind(text, months, kind):
    found = parse_requirement(text)
    assert (found.minimum_months, found.kind) == (months, kind)
    assert found.exact is True and minimum_months(text) == months


def test_fractional_requirements_round_up_and_are_flagged():
    found = parse_requirement("1.3 years experience")
    assert found.minimum_months == 16 and found.exact is False  # 15.6 months
    assert parse_requirement("2-3 years experience").maximum_months == 36


@pytest.mark.parametrize(
    "text",
    [
        "6 month contract role with experience",
        "within 6 months of joining you will ship",
        "3 month probation; software experience",
        "Minimum of one task a week, about 10 hours a week",
        "A paid 12 month internship",
        "No numbers here",
    ],
)
def test_month_quantities_that_are_not_experience_are_ignored(text):
    assert parse_requirement(text) is None


def _resolve(requirement, approved_months=None, approved_years=None):
    description = f"Requirements\n{requirement}\nPython\nResponsibilities\nBuild Python APIs"
    profile = CandidateProfile(skills=["Python"], experience_months=approved_months)
    jobs, people = build_segments({"description": description}, profile.model_dump())
    by_text = {s.text: s.id for s in jobs if s.field.startswith("description")}
    quote = parse_requirement(requirement).quote
    response = JobResponse.model_validate(
        {
            "findings": [
                {"kind": "exp", "text": quote, "job_evidence_id": by_text[requirement]},
                {"kind": "req", "text": "Python", "job_evidence_id": by_text["Python"]},
                {
                    "kind": "duty",
                    "text": "Build Python APIs",
                    "job_evidence_id": by_text["Build Python APIs"],
                },
            ]
        }
    )
    return resolve_job_requirements(
        response, jobs, people, approved_years, experience_months=approved_months
    )[0]


@pytest.mark.parametrize(
    "requirement,state,gap",
    [
        ("6 months of experience", "MATCHED", 0),
        ("12 months of experience", "MATCHED", 0),
        ("1 year of experience", "MATCHED", 0),
        ("15 months of experience", "MATCHED", 0),
        ("1 year and 3 months of experience", "MATCHED", 0),
        ("16 months of experience", "UNMATCHED", 1),
        ("1.5 years of experience", "UNMATCHED", 3),
        ("2+ years of experience", "UNMATCHED", 9),
        ("3 years of experience", "UNMATCHED", 21),
        ("1-2 years of experience", "MATCHED", 0),
        ("2-3 years of experience", "UNMATCHED", 9),
    ],
)
def test_comparison_against_requirements_in_months_years_decimals_and_ranges(
    requirement, state, gap
):
    found = _resolve(requirement, 15)
    assert found["match_state"] == state
    comparison = found["experience_comparison"]
    assert comparison["approved_months"] == 15
    assert comparison["approved_display"] == "1 year, 3 months"
    assert comparison["gap_months"] == gap and comparison["met"] is (state == "MATCHED")
    assert comparison["scope"] == "total"


def test_legacy_decimal_years_argument_gives_the_same_comparison():
    by_years = resolve_job_requirements_legacy("2+ years of experience", 1.25)
    by_months = _resolve("2+ years of experience", 15)
    assert by_years["match_state"] == by_months["match_state"] == "UNMATCHED"
    assert by_years["experience_comparison"] == by_months["experience_comparison"]


def resolve_job_requirements_legacy(requirement, years):
    description = f"Requirements\n{requirement}\nPython\nResponsibilities\nBuild Python APIs"
    profile = CandidateProfile(skills=["Python"], experience_years=years)
    jobs, people = build_segments({"description": description}, profile.model_dump())
    by_text = {s.text: s.id for s in jobs if s.field.startswith("description")}
    response = JobResponse.model_validate(
        {
            "findings": [
                {
                    "kind": "exp",
                    "text": parse_requirement(requirement).quote,
                    "job_evidence_id": by_text[requirement],
                },
                {"kind": "req", "text": "Python", "job_evidence_id": by_text["Python"]},
                {
                    "kind": "duty",
                    "text": "Build Python APIs",
                    "job_evidence_id": by_text["Build Python APIs"],
                },
            ]
        }
    )
    return resolve_job_requirements(response, jobs, people, years)[0]


def test_missing_approved_experience_is_unverified_not_zero_months():
    found = _resolve("2+ years of experience", None)
    assert found["match_state"] == "UNVERIFIED"
    assert found["experience_comparison"]["approved_display"] is None
    assert found["experience_comparison"]["gap_display"] is None


def test_total_experience_never_proves_technology_specific_years():
    found = _resolve("3+ years of Python experience", 60)
    assert found["match_state"] == "UNVERIFIED"
    assert found["experience_comparison"]["scope"] == "technology"
    assert experience_scope("3+ years of Python experience") == "technology"
    assert experience_scope("18 months of professional Python experience") == "technology"
    assert experience_scope("2+ years of professional software engineering") == "total"


def test_preferred_experience_is_labeled_not_presented_as_a_minimum():
    found = _resolve("Ideally 5 years of experience", 15)
    assert found["experience_comparison"]["requirement_kind"] == "preferred"
    assert found["experience_comparison"]["gap_display"] == "3 years, 9 months"


def test_job22_requirement_is_24_months_against_15_approved_with_a_9_month_gap():
    description = JOB22.read_text(encoding="utf-8")
    inventory = extract_requirements(description)
    profile = CandidateProfile(
        experience=[
            ProfileEntry(text="Software Engineer - Dubizzle Labs", dates=["2025", "Present"]),
            ProfileEntry(text="Software Engineer - Dubizzle Labs (continued)", dates=["2025"]),
        ],
        skills=["Python", "Docker"],
        experience_years=1.25,  # the legacy form of the approved profile
    )
    _, segments = build_segments({"description": description}, profile.model_dump())
    resolved = resolve_classifications(
        ClassifiedRequirements.model_validate({"items": _classifications(inventory)}),
        inventory,
        segments,
        None,
        experience_months=profile.experience_months,
    )
    exp = [r for r in resolved if r["kind"] == "exp" and "experience_comparison" in r][0]
    assert exp["text"] == "2+ years" and exp["match_state"] == "UNMATCHED"
    assert exp["derived_fields"] == ["experience_state_from_approved_years"]
    comparison = exp["experience_comparison"]
    assert (comparison["required_months"], comparison["approved_months"]) == (24, 15)
    assert comparison["gap_months"] == 9 and comparison["gap_display"] == "9 months"
    assert (comparison["required_display"], comparison["approved_display"]) == (
        "2 years",
        "1 year, 3 months",
    )
    assert profile.experience_months == 15  # two Dubizzle sections, one approved period


def test_month_based_requirements_are_not_missed_by_the_coverage_check():
    description = "Requirements\n18 months of professional experience\nPython\nResponsibilities\nBuild Python APIs"
    profile = CandidateProfile(skills=["Python"], experience_months=15)
    jobs, people = build_segments({"description": description}, profile.model_dump())
    by_text = {s.text: s.id for s in jobs if s.field.startswith("description")}
    omitted = JobResponse.model_validate(
        {
            "findings": [
                {"kind": "req", "text": "Python", "job_evidence_id": by_text["Python"]},
                {
                    "kind": "duty",
                    "text": "Build Python APIs",
                    "job_evidence_id": by_text["Build Python APIs"],
                },
            ]
        }
    )
    with pytest.raises(EvidenceError, match="missing_experience_requirement"):
        resolve_job_requirements(omitted, jobs, people, None, experience_months=15)


def _legacy_profile_row(store):
    """Insert the approved profile exactly as it was stored before months existed."""
    ensure_schema(store)
    legacy = CandidateProfile(experience_years=1.25, skills=["Docker", "Git", "Linux", "Python"])
    stored = json.loads(profile_json(legacy))
    stored.pop("experience_months")
    stored["experience_years"] = 1.25
    text = json.dumps(stored, sort_keys=True, ensure_ascii=False)
    with store.db:
        store.db.execute(
            "INSERT INTO candidate_profiles(created_at,approved,profile_hash,profile) "
            "VALUES (?,?,?,?)",
            ("2026-10-27", 1, profile_hash(legacy), text),
        )
    return text


def test_reapproving_the_unchanged_profile_in_months_keeps_assessments_current(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    with Store(":memory:") as store:
        store.upsert(_job22())
        from job_scraper.processing import process_jobs

        process_jobs(store)
        original = _legacy_profile_row(store)
        assert analyze(store, RESCORE_CONFIG, live=True, client_factory=_factory())["saved"] == 1
        before = store.db.execute("SELECT * FROM ai_assessments").fetchall()
        assert results(store, RESCORE_CONFIG)[0]["stale"] is False
        # Preview and export show months; the stored row is not rewritten.
        approved = latest_profile(store, approved_only=True)
        assert approved["experience"]["display"] == "1 year, 3 months"
        assert approved["profile"]["experience_years"] == 1.25
        review = tmp_path / "review.json"
        assert export_profile(store, review)["experience"]["months"] == 15
        exported = json.loads(review.read_text(encoding="utf-8"))
        assert exported["experience_months"] == 15 and "experience_years" not in exported
        stored_before = store.db.execute(
            "SELECT profile FROM candidate_profiles WHERE version=1"
        ).fetchone()[0]
        outcome = approve_profile(store, review)
        assert outcome["profile_hash"] == approved["profile_hash"]
        assert outcome["experience"]["source"] == "experience_months"
        # Version 1 is byte-identical; version 2 is the new months form with the same hash.
        assert (
            store.db.execute("SELECT profile FROM candidate_profiles WHERE version=1").fetchone()[0]
            == stored_before
            == original
        )
        assert (
            "experience_months"
            in store.db.execute(
                "SELECT profile FROM candidate_profiles WHERE version=2"
            ).fetchone()[0]
        )
        # Same hash, so nothing is stale; the cache hits; offline rescoring has nothing to do.
        assert results(store, RESCORE_CONFIG)[0]["stale"] is False
        factory = _factory()
        again = analyze(store, RESCORE_CONFIG, live=True, client_factory=factory)
        assert (again["cached"], again["requests"]) == (1, 0)
        factory.assert_not_called()
        assert rescore(store, RESCORE_CONFIG)["cached"] == 1
        assert store.db.execute("SELECT * FROM ai_assessments").fetchall() == before


def test_a_changed_amount_of_experience_is_a_new_profile_and_makes_assessments_stale(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    with Store(":memory:") as store:
        store.upsert(_job22())
        from job_scraper.processing import process_jobs

        process_jobs(store)
        _legacy_profile_row(store)
        analyze(store, RESCORE_CONFIG, live=True, client_factory=_factory())
        review = tmp_path / "review.json"
        export_profile(store, review)
        data = json.loads(review.read_text(encoding="utf-8"))
        data["experience_months"] = 16
        review.write_text(json.dumps(data), encoding="utf-8")
        approve_profile(store, review)
        assert results(store, RESCORE_CONFIG)[0]["stale"] is True
        assert store.db.execute("SELECT count(*) FROM ai_assessments").fetchone()[0] == 1


def test_months_profile_scores_and_rescores_exactly_like_the_years_profile(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    outcomes = []
    for profile in (
        matching_profile(experience_years=1.25),
        matching_profile(experience_months=15),
    ):
        with Store(":memory:") as store:
            _setup(store, profile)
            _historic(store, monkeypatch)
            assert rescore(store, RESCORE_CONFIG)["saved"] == 1
            rows = store.db.execute("SELECT * FROM ai_assessments ORDER BY id").fetchall()
            new = json.loads(rows[1]["result"])
            outcomes.append(
                (
                    rows[0]["profile_hash"],
                    new["score"],
                    new["breakdown"],
                    [(f["kind"], f["match_state"]) for f in new["referenced_findings"]],
                )
            )
    assert outcomes[0] == outcomes[1]


def matching_profile(**experience):
    return CandidateProfile(
        experience=[ProfileEntry(text="Built Ruby on Rails APIs professionally")],
        projects=[
            ProfileEntry(
                text="DeepFake Detection | Python, PyTorch. Built Python model training pipeline"
            )
        ],
        skills=["Docker", "Git", "Linux", "Python"],
        **experience,
    )


def test_ranking_output_is_the_same_for_equivalent_experience_values():
    rendered = []
    for form in ({"experience_years": 1.25}, {"experience_months": 15}):
        with Store(":memory:") as store:
            ids = _seed(store, [_job("a"), _job("b", description="Build Python APIs. Remote.")])
            profile = CandidateProfile(skills=["Python"], **form)
            with store.db:
                store.db.execute("UPDATE candidate_profiles SET approved=0")
                store.db.execute(
                    "INSERT INTO candidate_profiles(created_at,approved,profile_hash,profile) "
                    "VALUES (?,?,?,?)",
                    ("2026-11-02", 1, profile_hash(profile), profile_json(profile)),
                )
            _assess(store, ids["a"], _items(("req", M), ("exp", U), ("duty", M)))
            data = ranking.rank(store, CONFIG)
            data["approved_experience"].pop("source")
            rendered.append(json.dumps(data, sort_keys=True))
    assert rendered[0] == rendered[1]


def test_ranking_explains_experience_gaps_in_years_and_months_without_new_data():
    with Store(":memory:") as store:
        ids = _seed(store, [_job("a")])  # approves 15 months
        items = _items(("req", M), ("exp", U), ("duty", M), ("exp", "UNVERIFIED"))
        items[1]["text"] = "2+ years"  # saved before months existed: no stored comparison
        assert "experience_comparison" not in items[1]
        _assess(store, ids["a"], items)
        detail = ranking.explain(store, CONFIG, ids["a"])
        gap = detail["confirmed_gaps"][0]["experience"]
        assert (gap["required_display"], gap["approved_display"], gap["gap_display"]) == (
            "2 years",
            "1 year, 3 months",
            "9 months",
        )
        assert (gap["required_months"], gap["approved_months"], gap["gap_months"]) == (24, 15, 9)
        # Vague wording is not given a number, and is never shown as zero months.
        vague = detail["insufficient_information"]["unresolved_requirements"][0]
        assert vague["experience"] is None
        assert detail["approved_experience"]["months"] == 15
        text = ranking.render_explain(detail)
        assert "Approved experience: 1 year, 3 months (15 months;" in text
        assert "required 2 years; approved 1 year, 3 months; gap 9 months" in text
        # Public JSON keeps its earlier fields.
        for key in ("technical", "module2", "confirmed_gaps", "insufficient_information", "flags"):
            assert key in detail
        assert (
            detail["technical"]["score"]
            == ranking.rank(store, CONFIG)["ranked"][0]["technical"]["score"]
        )


def test_ranking_labels_a_preferred_level_instead_of_calling_it_a_minimum():
    with Store(":memory:") as store:
        ids = _seed(store, [_job("a")])
        items = _items(("req", M), ("exp", U), ("duty", M))
        items[1]["text"] = "5 years"
        items[1]["job_excerpt"] = "Ideally 5 years of experience"
        _assess(store, ids["a"], items)
        detail = ranking.explain(store, CONFIG, ids["a"])
        view = detail["confirmed_gaps"][0]
        assert view["experience"]["requirement_kind"] == "preferred"
        assert view["reason"] == "approved_experience_below_preferred_level"
        assert view["experience"]["gap_display"] == "3 years, 9 months"
        assert "(preferred; approved 1 year, 3 months" not in ranking.render_explain(detail)
        assert "required 5 years (preferred); approved 1 year, 3 months; gap 3 years, 9 months" in (
            ranking.render_explain(detail)
        )


def test_module2_screening_does_not_depend_on_the_experience_representation():
    from test_ranking import ELIGIBLE_TEXT

    import job_scraper.screening as screening

    assert VERSION == "pakistan-0-3-v3.6"
    assert "experience_months" not in Path(screening.__file__).read_text(encoding="utf-8")
    result = classify(_job("x", description=ELIGIBLE_TEXT))
    assert result.status == "ELIGIBLE"
    assert result.decisions["experience"].status == "ELIGIBLE"
    assert result.experience_requirements[0]["minimum_years"] == 2  # Module 2 keeps its units


# ---------------------------------------------------------------------------------------
# Final compatibility review: hashing, rule versions and what counts as a minimum
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "years,months", [(1.25, 15), (1.5, 18), (2.0, 24), (2, 24), (0.5, 6), (3.0, 36)]
)
def test_equivalent_experience_representations_hash_identically(years, months):
    base = {"skills": ["Python"], "technologies": ["Python"]}
    assert profile_hash(CandidateProfile(**base, experience_years=years)) == profile_hash(
        CandidateProfile(**base, experience_months=months)
    )


def test_different_approved_experience_always_hashes_differently():
    base = {"skills": ["Python"]}
    hashes = {m: profile_hash(CandidateProfile(**base, experience_months=m)) for m in range(961)}
    assert len(set(hashes.values())) == 961  # every whole month count is its own profile
    assert hashes[15] != hashes[18] != hashes[24] and hashes[15] != hashes[24]
    assert hashes[15] != profile_hash(CandidateProfile(**base))  # unknown is not 15


@pytest.mark.parametrize("new_months", [18, 24])
def test_a_real_change_of_approved_experience_invalidates_the_cached_assessment(
    new_months, tmp_path, monkeypatch
):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    with Store(":memory:") as store:
        store.upsert(_job22())
        from job_scraper.processing import process_jobs

        process_jobs(store)
        _legacy_profile_row(store)
        assert analyze(store, RESCORE_CONFIG, live=True, client_factory=_factory())["saved"] == 1
        original = store.db.execute("SELECT * FROM ai_assessments").fetchone()
        review = tmp_path / "review.json"
        export_profile(store, review)
        data = json.loads(review.read_text(encoding="utf-8"))
        data["experience_months"] = new_months
        review.write_text(json.dumps(data), encoding="utf-8")
        approve_profile(store, review)
        assert results(store, RESCORE_CONFIG)[0]["stale"] is True
        factory = _factory()
        out = analyze(store, RESCORE_CONFIG, live=True, client_factory=factory)
        assert (out["cached"], out["requests"], out["saved"]) == (0, 1, 1)
        rows = store.db.execute("SELECT * FROM ai_assessments ORDER BY id").fetchall()
        assert tuple(rows[0]) == tuple(original)  # the earlier assessment is untouched
        assert rows[1]["profile_hash"] != original["profile_hash"]
        comparison = [
            f["experience_comparison"]
            for f in json.loads(rows[1]["result"])["referenced_findings"]
            if "experience_comparison" in f
        ][0]
        assert comparison["approved_months"] == new_months
        # The new profile has a current assessment, so offline rescoring has nothing to do.
        assert rescore(store, RESCORE_CONFIG)["cached"] == 1


def test_the_rule_hash_separates_the_old_and_new_experience_rules():
    current = matching._rule_hash(CONFIG)
    previous = matching._rule_hash(CONFIG, scoring="score-v5", evidence="evidence-v8")
    assert current != previous
    assert (matching.SCORING_VERSION, matching.EVIDENCE_VERSION) == ("score-v6", "evidence-v9")


def test_an_assessment_saved_under_the_earlier_experience_rules_is_stale_and_rescorable_offline(
    monkeypatch,
):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    with Store(":memory:") as store:
        _setup(store, matching_profile(experience_months=15))
        old = _historic(
            store, monkeypatch, SCORING_VERSION="score-v5", EVIDENCE_VERSION="evidence-v8"
        )
        assert json.loads(old["result"])["evidence_version"] == "evidence-v8"
        assert results(store, RESCORE_CONFIG)[0]["stale"] is True
        frozen = tuple(old)
        monkeypatch.setattr(matching.genai, "Client", lambda *a, **k: 1 / 0)
        outcome = rescore(store, RESCORE_CONFIG)
        assert (outcome["saved"], outcome["requests"], outcome["skipped"]) == (1, 0, [])
        rows = store.db.execute("SELECT * FROM ai_assessments ORDER BY id").fetchall()
        assert tuple(rows[0]) == frozen
        new = json.loads(rows[1]["result"])
        assert (new["scoring_version"], new["evidence_version"]) == ("score-v6", "evidence-v9")
        assert new["provenance"]["source_evidence_version"] == "evidence-v8"
        assert [x["stale"] for x in results(store, RESCORE_CONFIG)] == [False, True]


def _pipeline(line, section="Requirements", approved_months=15):
    """Gemini labels `line` as an experience item; the production path scores it."""
    if section == "Requirements":
        text = f"Requirements\n{line}\nPython\nResponsibilities\nBuild Python APIs"
    elif section == "Nice to have":
        text = f"Requirements\nPython\nNice to have\n{line}\nResponsibilities\nBuild Python APIs"
    else:
        text = f"Requirements\nPython\nResponsibilities\nBuild Python APIs\nTerms\n{line}"
    profile = CandidateProfile(skills=["Python"], experience_months=approved_months)
    inventory = extract_requirements(text)
    items = []
    for item in inventory:
        if item.heading:
            kinds = ["nonreq"]
        elif item.text == line:
            kinds = ["nonreq"] if section == "Terms" else ["exp"]
        elif item.text == "Python":
            kinds = ["req"]
        elif item.text == "Build Python APIs":
            kinds = ["duty"]
        else:
            kinds = ["nonreq"]
        items.append({"requirement_id": item.id, "parts": [{"kind": k} for k in kinds]})
    _, segments = build_segments({"description": text}, profile.model_dump())
    resolved = resolve_classifications(
        ClassifiedRequirements.model_validate({"items": items}),
        inventory,
        segments,
        None,
        experience_months=approved_months,
    )
    return resolved, matching.score_resolved(resolved, CONFIG, inventory)


def _exp(resolved):
    return [r for r in resolved if r["kind"] == "exp"]


@pytest.mark.parametrize(
    "line,section",
    [
        ("Ideally 5 years of experience", "Requirements"),
        ("Preferably 3 years of professional experience", "Requirements"),
        ("3+ years of experience", "Nice to have"),
    ],
)
def test_preferred_experience_is_never_scored_as_a_minimum(line, section):
    resolved, scored = _pipeline(line, section)
    found = _exp(resolved)[0]
    assert found["experience_comparison"]["requirement_kind"] == "preferred"
    assert "preferred_experience_not_a_minimum" in found["derived_fields"]
    assert "experience_state_from_approved_years" not in found["derived_fields"]
    assert scored["breakdown"]["experience"]["status"] == "not_stated"
    assert [e["reason"] for e in scored["excluded_findings"]] == [
        "experience_level_is_not_a_minimum"
    ]
    # More or less experience cannot change the score through a preference.
    assert _pipeline(line, section, 120)[1]["score"] == scored["score"]


@pytest.mark.parametrize("line", ["Up to 2 years of experience", "At most 18 months of experience"])
def test_maximum_experience_is_never_a_minimum(line):
    resolved, scored = _pipeline(line)
    found = _exp(resolved)[0]
    assert found["match_state"] == "UNVERIFIED"
    assert found["experience_comparison"]["requirement_kind"] == "maximum"
    assert "maximum_experience_not_a_minimum" in found["derived_fields"]
    assert scored["breakdown"]["experience"]["status"] == "not_stated"


def test_or_equivalent_wording_is_not_a_confirmed_gap():
    below = _exp(_pipeline("5 years of experience or equivalent", approved_months=15)[0])[0]
    above = _exp(_pipeline("5 years of experience or equivalent", approved_months=60)[0])[0]
    assert (below["match_state"], above["match_state"]) == ("UNVERIFIED", "MATCHED")
    assert "experience_wording_ambiguous" in below["derived_fields"]


@pytest.mark.parametrize(
    "line,months",
    [
        ("3 years of experience required, Python preferred", 36),
        ("3+ years of experience (5 preferred)", 36),
        ("Required: 18 months of professional experience; Python preferred", 18),
    ],
)
def test_a_preference_elsewhere_in_the_line_does_not_weaken_a_mandatory_minimum(line, months):
    quantity = parse_requirement(line)
    assert (quantity.minimum_months, quantity.kind) == (months, "minimum")


@pytest.mark.parametrize(
    "line",
    [
        "A 6 month contract with possible extension",
        "3 month probation; experience reviewed",
        "You will ship within 6 months of joining",
    ],
)
def test_durations_that_are_not_experience_are_never_requirements(line):
    resolved, scored = _pipeline(line, "Terms")
    assert _exp(resolved) == []
    assert scored["breakdown"]["experience"]["status"] == "not_stated"


@pytest.mark.parametrize(
    "line,state,points",
    [
        ("6 months of experience", "MATCHED", 25.0),
        ("18 months of professional experience", "UNMATCHED", 0.0),
        ("1 year and 6 months of experience", "UNMATCHED", 0.0),
    ],
)
def test_month_based_minimums_are_scored_not_dropped_as_vague_wording(line, state, points):
    resolved, scored = _pipeline(line)
    assert _exp(resolved)[0]["match_state"] == state
    experience = scored["breakdown"]["experience"]
    assert experience["status"] == "scored" and experience["total"] == 1
    assert experience["points"] == points
    assert scored["excluded_findings"] == []


def test_a_range_is_scored_from_its_lower_bound():
    below = _exp(_pipeline("2-3 years of experience", approved_months=23)[0])[0]
    inside = _exp(_pipeline("2-3 years of experience", approved_months=24)[0])[0]
    assert (below["match_state"], inside["match_state"]) == ("UNMATCHED", "MATCHED")
    assert inside["experience_comparison"]["requirement_kind"] == "range"


def test_job22_is_unchanged_under_the_new_versions_and_earns_no_extra_credit():
    description = JOB22.read_text(encoding="utf-8")
    inventory = extract_requirements(description)
    profile = CandidateProfile(skills=["Docker", "Git"], experience_months=15)
    _, segments = build_segments({"description": description}, profile.model_dump())
    resolved = resolve_classifications(
        ClassifiedRequirements.model_validate({"items": _classifications(inventory)}),
        inventory,
        segments,
        None,
        experience_months=15,
    )
    scored = matching.score_resolved(resolved, CONFIG, inventory)
    exp = _exp(resolved)[0]
    assert (exp["text"], exp["match_state"]) == ("2+ years", "UNMATCHED")
    comparison = exp["experience_comparison"]
    assert (comparison["required_months"], comparison["approved_months"]) == (24, 15)
    assert (comparison["gap_months"], comparison["gap_display"]) == (9, "9 months")
    assert scored["breakdown"]["experience"]["unmatched"] == 1
    # Listed-only and unknown skills still earn nothing; no new credit appeared.
    credited = [
        r for r in resolved if r["kind"] != "exp" and r["match_state"] in {"MATCHED", "PARTIAL"}
    ]
    assert credited == [] and scored["score"] == 0
