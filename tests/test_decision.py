"""Module 3 final suitability decision: synthetic rows only, no database or Gemini."""

import pytest

from job_scraper.decision import EXPERIENCE_GAP_INELIGIBLE_MONTHS, decide


def row(score=0, ceiling=85, coverage=15, findings=(), restricted=False, status="current"):
    return {
        "assessment_status": status,
        "restricted": restricted,
        "module2": {"status": "UNVERIFIED"},
        "technical": {"score": score, "score_ceiling": ceiling, "evidence_coverage": coverage},
        "findings": list(findings),
    }


def experience(required, approved=15, state="UNMATCHED", kind="minimum", scope="total"):
    gap = max(0, required - approved)
    return {
        "kind": "exp",
        "match_state": state,
        "experience_comparison": {
            "requirement_kind": kind,
            "scope": scope,
            "required_months": required,
            "required_display": f"{required} months",
            "approved_display": f"{approved} months",
            "gap_months": gap,
            "gap_display": f"{gap} months",
        },
    }


def test_no_current_assessment_has_no_decision():
    assert decide(row(status="stale")) is None
    assert decide(row(status="missing")) is None


def test_low_confidence_zero_is_unverified_not_ineligible():
    result = decide(row(score=0, ceiling=85, coverage=15))
    assert result["status"] == "UNVERIFIED"
    assert result["reason_code"] == "insufficient_evidence"


def test_high_score_is_eligible_but_pakistan_hiring_stays_separate():
    result = decide(row(score=72, ceiling=90, coverage=80))
    assert result["status"] == "ELIGIBLE"
    assert result["pakistan_hiring"] == "UNVERIFIED"


def test_ceiling_below_threshold_is_ineligible():
    result = decide(row(score=20, ceiling=45, coverage=90))
    assert (result["status"], result["reason_code"]) == ("INELIGIBLE", "cannot_reach_suitable_fit")


def test_five_year_minimum_is_ineligible_even_with_high_score():
    result = decide(row(score=80, ceiling=95, findings=[experience(60)]))
    assert (result["status"], result["reason_code"]) == ("INELIGIBLE", "experience_gap")


def test_small_gap_is_not_ineligible():
    # 24 months required vs 15 approved: gap 9, below the limit.
    assert decide(row(score=2, ceiling=73, findings=[experience(24)]))["status"] == "UNVERIFIED"
    assert 9 < EXPERIENCE_GAP_INELIGIBLE_MONTHS


def test_gap_at_the_limit_is_ineligible():
    required = 15 + EXPERIENCE_GAP_INELIGIBLE_MONTHS
    assert decide(row(score=70, ceiling=90, findings=[experience(required)]))["status"] == (
        "INELIGIBLE"
    )


@pytest.mark.parametrize(
    "item",
    [
        experience(60, kind="preferred"),
        experience(60, kind="maximum"),
        experience(60, kind="ambiguous"),
        experience(60, scope="technology"),
        experience(60, state="UNVERIFIED"),
    ],
)
def test_non_binding_experience_never_decides(item):
    assert decide(row(score=70, ceiling=90, findings=[item]))["status"] == "ELIGIBLE"


def test_manual_restriction_overrides_a_high_score():
    result = decide(row(score=90, ceiling=95, restricted=True))
    assert (result["status"], result["reason_code"]) == ("INELIGIBLE", "manual_restriction")


def test_missing_skill_alone_does_not_make_ineligible():
    skill = {"kind": "req", "match_state": "UNMATCHED"}
    assert decide(row(score=65, ceiling=80, findings=[skill]))["status"] == "ELIGIBLE"


def _scoped(excerpt, quote, gap=21):
    item = experience(15 + gap)
    return {**item, "text": quote, "job_excerpt": excerpt, "job_evidence_id": "R012"}


@pytest.mark.parametrize(
    "excerpt, quote, status",
    [
        ("- Strong Haskell skills with at least 3 years of experience.", "3 years", "UNVERIFIED"),
        (
            "- Have 3+ years of experience building and operating cloud infrastructure "
            "or distributed systems at scale;",
            "3+ years",
            "UNVERIFIED",
        ),
        (
            "3+ years of experience as a full-stack or frontend-heavy engineer.",
            "3+ years",
            "INELIGIBLE",
        ),
        ("- 5+ years of experience in software engineering", "5+ years", "INELIGIBLE"),
        ("- 3+ years of experience building web applications", "3+ years", "INELIGIBLE"),
    ],
)
def test_years_tied_to_a_skill_or_area_never_decide(excerpt, quote, status):
    result = decide(row(score=7, ceiling=71, findings=[_scoped(excerpt, quote)]))
    assert result["status"] == status
    if status == "UNVERIFIED":
        assert "specific skill or area" in result["detail"] and "scoped years" in result["detail"]


def test_evidence_scope_marks_skill_and_area_years_at_the_source():
    from job_scraper.evidence import experience_scope

    assert (
        experience_scope("- Strong Haskell skills with at least 3 years of experience.")
        == "technology"
    )
    assert (
        experience_scope(
            "- Have 3+ years of experience building and operating cloud infrastructure at scale;"
        )
        == "technology"
    )
    assert (
        experience_scope("3+ years of experience as a full-stack or frontend-heavy engineer.")
        == "total"
    )
    assert experience_scope("- 5+ years of experience in software engineering") == "total"


def _verdicts(*pairs):
    return [
        {
            "requirement_id": f"R{n:03d}",
            "kinds": ["req"],
            "verdict": v,
            "job_quote": "x",
            "cv_evidence": [],
        }
        for n, v in enumerate(pairs, 1)
    ]


def _unverified_row(verdicts, codes=("core_unverified",), score=10, ceiling=80):
    base = row(score=score, ceiling=ceiling)
    base["judgment"] = {
        "id": 9,
        "suggested": "UNVERIFIED",
        "summary": "s",
        "reason_codes": list(codes),
        "blocking_requirement_ids": [],
    }
    base["judgment_requirements"] = verdicts
    return base


def test_out_of_scope_role_types_become_ineligible():
    result = decide(
        _unverified_row(_verdicts("met", "met"), codes=("not_software_engineering_scope",))
    )
    assert (result["status"], result["reason_code"], result["source"]) == (
        "INELIGIBLE",
        "out_of_scope",
        "policy",
    )
    assert (
        decide(_unverified_row(_verdicts("met"), codes=("research_level_role_unverified",)))[
            "reason_code"
        ]
        == "out_of_scope"
    )
    # a missing skill code is NOT a role-type code
    assert (
        decide(_unverified_row(_verdicts("met"), codes=("linux_unverified",)))["status"]
        != "INELIGIBLE"
    )


def test_good_enough_needs_share_no_unmet_no_open_location_and_a_viable_score():
    ok = decide(_unverified_row(_verdicts("met", "partial", "partial", "unclear", "met")))
    assert (ok["status"], ok["reason_code"]) == ("ELIGIBLE", "good_enough_fit")
    assert "4 of 5" in ok["detail"]
    assert (
        decide(_unverified_row(_verdicts("met", "met", "met", "not_met")))["status"] != "ELIGIBLE"
    )
    assert (
        decide(
            _unverified_row(_verdicts("met", "met", "met"), codes=("office_location_unverified",))
        )["status"]
        == "UNVERIFIED"
    )
    assert (
        decide(_unverified_row(_verdicts("met", "met", "met"), score=5, ceiling=40))["status"]
        == "INELIGIBLE"
    )  # the score rules still say it cannot reach a suitable fit
    assert (
        decide(_unverified_row(_verdicts("met", "met")))["reason_code"] != "good_enough_fit"
    )  # too few items
    assert (
        decide(_unverified_row(_verdicts("met", "partial", "unclear", "unclear")))["status"]
        == "UNVERIFIED"  # 50%: neither good enough nor poor fit
    )


def test_poor_fit_needs_enough_items_and_a_low_supported_share():
    bad = decide(_unverified_row(_verdicts("unclear", "unclear", "unclear", "unclear", "partial")))
    assert (bad["status"], bad["reason_code"]) == ("INELIGIBLE", "poor_fit")
    assert "1 of 5" in bad["detail"]
    assert (
        decide(_unverified_row(_verdicts("unclear", "unclear", "unclear")))["status"]
        == "UNVERIFIED"
    )
    assert (
        decide(_unverified_row(_verdicts("unclear", "partial", "partial", "unclear")))["status"]
        == "UNVERIFIED"
    )


def test_policy_rules_never_override_hard_blockers_or_a_confirmed_judgment():
    blocked = _unverified_row(_verdicts("met", "met", "met", "met"))
    blocked["restricted"] = True
    assert decide(blocked)["reason_code"] == "manual_restriction"
    eligible = _unverified_row(_verdicts("unclear", "unclear", "unclear", "unclear", "unclear"))
    eligible["judgment"]["suggested"] = "ELIGIBLE"
    assert decide(eligible)["reason_code"] == "session_judgment_eligible"
