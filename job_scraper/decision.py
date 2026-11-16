"""Module 3 final application-suitability decision (derived, never stored).

Module 2 is the preliminary screen: a definite INELIGIBLE never reaches Module 3.
For the remaining jobs (Module 2 UNVERIFIED or ELIGIBLE) this module turns a current,
validated assessment into ELIGIBLE / INELIGIBLE / UNVERIFIED *suitability for applying*.

It is a pure function of a ranking row: no database write, no Gemini request, no change
to Module 2, profiles or assessments. It does NOT establish that an employer can hire a
person in Pakistan; that stays the separate Module 2 status carried along unchanged.

Hard blockers always win, in this order:

1. current manual `confirmed_restricted` review      -> INELIGIBLE manual_restriction
2. confirmed total-experience minimum at least
   EXPERIENCE_GAP_INELIGIBLE_MONTHS above approved   -> INELIGIBLE experience_gap

Score rules (the deterministic evidence score and its ceiling):

3. score ceiling below HIGH_SCORE                    -> INELIGIBLE cannot_reach_suitable_fit
   (even if every unresolved item resolved in the candidate's favor the job stays low)
4. score at least HIGH_SCORE                         -> ELIGIBLE suitable_fit
5. otherwise                                         -> UNVERIFIED insufficient_evidence

When a current, validated session judgment exists (`judge.py`, row["judgment"]) it is
combined with the score rules after the hard blockers; the judgment was written by a
labeler that read the approved profile, the score rules are the deterministic cross-check:

* judgment INELIGIBLE (validated: Python-confirmed experience gap or a quoted unmet hiring
  condition)                                        -> INELIGIBLE session_judgment_ineligible
* judgment ELIGIBLE and score rules INELIGIBLE       -> UNVERIFIED judgment_conflicts_with_score
* judgment ELIGIBLE otherwise                        -> ELIGIBLE session_judgment_eligible
* judgment UNVERIFIED and score rules INELIGIBLE     -> INELIGIBLE cannot_reach_suitable_fit
* judgment UNVERIFIED otherwise                      -> UNVERIFIED session_judgment_unverified
  (the more cautious of the two wins; a high score alone does not override it)

Years tied to one skill or area of work ("Haskell skills with at least 3 years", "3+ years
building cloud infrastructure") never decide; they only add a note (`scoped_years`).
Three policy rules (approved by the user 2026-11-16) resolve a judgment that says UNVERIFIED when its
stored verdicts support a clear outcome, so Unverified means "genuinely unclear", not "not a fit":

* OUT OF SCOPE: the judgment's reason codes name a role type outside software engineering (support, QA,
  developer relations, research-level, FinOps, hardware enablement ...)   -> INELIGIBLE out_of_scope
* GOOD ENOUGH: at least GOOD_ENOUGH_SHARE of the mandatory (`req`) items are met or partial, none of the
  mandatory/experience/eligibility items is unmet, no location/office/language question is open, and the
  score rules do not say INELIGIBLE                                    -> ELIGIBLE good_enough_fit
* POOR FIT: at most POOR_FIT_SHARE of at least MIN_POOR_FIT_ITEMS mandatory items are supported by the
  approved profile                                                      -> INELIGIBLE poor_fit
Missing evidence is not proof of a missing skill: these rules only read what the approved profile shows,
the tag says which rule fired, and every verdict stays in the stored judgment for review.

A missing CV skill alone never decides INELIGIBLE; a job without a current assessment
gets no decision (it is never scored or judged as zero).
"""

import re

from .experience import scoped_years_text
from .matching import HIGH_SCORE

DECISION_VERSION = "final-v4"
# A required total-experience minimum this many months above the approved experience is a
# substantial, non-negotiable gap. A judgment, not derived from data; change it here.
EXPERIENCE_GAP_INELIGIBLE_MONTHS = 12
GOOD_ENOUGH_SHARE = 0.6
MIN_GOOD_ENOUGH_ITEMS = 3
POOR_FIT_SHARE = 0.25
MIN_POOR_FIT_ITEMS = 4
OUT_OF_SCOPE_CODES = frozenset(
    {
        "not_software_engineering_scope",
        "research_level_role_unverified",
        "research_role_unverified",
        "web_scale_ml_research_unverified",
        "developer_relations_role",
        "support_engineering_scope",
        "finops_scope_mismatch",
        "scope_mismatch_with_profile",
        "maintenance_engineering_scope",
        "partner_hardware_enablement_scope",
    }
)
LOCATION_OPEN = re.compile(r"location|office|language|visa|citizen|residen|apac|emea|relocat", re.I)
SEMANTICS = (
    "Final Module 3 suitability for applying, from the current assessment. It is not "
    "employer-confirmed hiring eligibility in Pakistan; Module 2 status is shown separately."
)


def scoped_years(item) -> bool:
    """True when the item's years qualify a named skill or an area of work, not the career.
    Older saved assessments predate evidence-v11, which marks such years at the source."""
    return scoped_years_text(item.get("job_excerpt") or "", item.get("text") or "")


def _total_minimum(item) -> dict | None:
    """The comparison of an unmet total-experience minimum, or None."""
    view = item.get("experience_comparison") or {}
    if (
        item.get("kind") == "exp"
        and item.get("match_state") == "UNMATCHED"
        and view.get("requirement_kind") in {"minimum", "range"}
        and view.get("scope") != "technology"
        and view.get("gap_months") is not None
        and not scoped_years(item)
    ):
        return view
    return None


def _experience_gap(findings) -> dict | None:
    """Largest confirmed gap against a total-experience minimum, or None."""
    worst = None
    for item in findings:
        view = _total_minimum(item)
        if view and (worst is None or view["gap_months"] > worst["gap_months"]):
            worst = view
    return worst


def _scoped_note(findings) -> str:
    """A note for years tied to a skill or area that exceed the career, which never decide."""
    for item in findings:
        view = item.get("experience_comparison") or {}
        if (
            item.get("kind") == "exp"
            and view.get("requirement_kind") in {"minimum", "range"}
            and (view.get("gap_months") or 0) >= EXPERIENCE_GAP_INELIGIBLE_MONTHS
            and (view.get("scope") == "technology" or scoped_years(item))
        ):
            return (
                f" Note: it asks for {view['required_display']} in a specific skill or area, "
                f"longer than your {view['approved_display']} in total; scoped years do not "
                "decide the tag."
            )
    return ""


def hard_blocker(findings, requirement_ids) -> bool:
    """True when one of the named requirements is a hard blocker: a stated hiring condition
    (kind eligibility) or a total-experience minimum at least the limit above approved."""
    for item in findings:
        if item.get("job_evidence_id") not in requirement_ids:
            continue
        if item.get("kind") == "eligibility":
            return True
        view = _total_minimum(item)
        if view and view["gap_months"] >= EXPERIENCE_GAP_INELIGIBLE_MONTHS:
            return True
    return False


def _result(status, code, detail, row, source="score_rules") -> dict:
    judgment = row.get("judgment")
    return {
        "status": status,
        "reason_code": code,
        "detail": detail,
        "version": DECISION_VERSION,
        "source": source,
        "judgment_id": judgment["id"] if judgment else None,
        "pakistan_hiring": row["module2"]["status"],
        "pakistan_hiring_note": "Module 2 status; not confirmed by the employer",
    }


def _by_score(technical) -> tuple[str, str, str]:
    if technical["score_ceiling"] < HIGH_SCORE:
        return (
            "INELIGIBLE",
            "cannot_reach_suitable_fit",
            f"Best possible score {technical['score_ceiling']} is below {HIGH_SCORE}.",
        )
    if technical["score"] >= HIGH_SCORE:
        return (
            "ELIGIBLE",
            "suitable_fit",
            f"Score {technical['score']} reaches {HIGH_SCORE} with no hard blocker.",
        )
    return (
        "UNVERIFIED",
        "insufficient_evidence",
        f"Score {technical['score']} but up to {technical['score_ceiling']} if unresolved "
        f"items resolve favorably (coverage {technical['evidence_coverage']}%).",
    )


def _policy(row: dict, judgment: dict, score_status: str, why: str) -> dict | None:
    """The three approved rules that resolve a judgment which says UNVERIFIED."""
    matched = sorted(set(judgment.get("reason_codes", [])) & OUT_OF_SCOPE_CODES)
    if matched:
        return _result(
            "INELIGIBLE",
            "out_of_scope",
            f"Role type outside software engineering ({', '.join(matched)}). {why}",
            row,
            "policy",
        )
    requirements = row.get("judgment_requirements") or []
    mandatory = [
        v for v in requirements if "req" in v["kinds"] and v["verdict"] != "not_applicable"
    ]
    supported = sum(v["verdict"] in {"met", "partial"} for v in mandatory)
    unmet = any(
        v["verdict"] == "not_met" and {"req", "exp", "eligibility"} & set(v["kinds"])
        for v in requirements
    )
    location_open = any(LOCATION_OPEN.search(c) for c in judgment.get("reason_codes", []))
    if (
        len(mandatory) >= MIN_GOOD_ENOUGH_ITEMS
        and supported / len(mandatory) >= GOOD_ENOUGH_SHARE
        and not unmet
        and not location_open
        and score_status != "INELIGIBLE"
    ):
        return _result(
            "ELIGIBLE",
            "good_enough_fit",
            f"{supported} of {len(mandatory)} mandatory requirements are met or partial and none "
            f"is unmet. {why}",
            row,
            "policy",
        )
    if len(mandatory) >= MIN_POOR_FIT_ITEMS and supported / len(mandatory) <= POOR_FIT_SHARE:
        return _result(
            "INELIGIBLE",
            "poor_fit",
            f"Only {supported} of {len(mandatory)} mandatory requirements are supported by the "
            f"approved profile. {why}",
            row,
            "policy",
        )
    return None


def _decide(row: dict) -> dict | None:
    technical = row.get("technical")
    if row.get("assessment_status") != "current" or technical is None:
        return None
    if row.get("restricted"):
        return _result(
            "INELIGIBLE",
            "manual_restriction",
            "A current evidence-backed manual review confirms a restriction.",
            row,
            "hard_blocker",
        )
    gap = _experience_gap(row.get("findings", []))
    if gap and gap["gap_months"] >= EXPERIENCE_GAP_INELIGIBLE_MONTHS:
        return _result(
            "INELIGIBLE",
            "experience_gap",
            f"Requires {gap['required_display']}; approved {gap['approved_display']} "
            f"(gap {gap['gap_display']}, limit {EXPERIENCE_GAP_INELIGIBLE_MONTHS} months).",
            row,
            "hard_blocker",
        )
    status, code, detail = _by_score(technical)
    judgment = row.get("judgment")
    if not judgment:
        return _result(status, code, detail, row)
    said = judgment["suggested"]
    why = f"Session judgment #{judgment['id']}: {judgment['summary']}"
    if said == "INELIGIBLE":
        if not hard_blocker(row.get("findings", []), set(judgment["blocking_requirement_ids"])):
            # An older row, or one whose blocker is a small experience gap: not a hard blocker.
            return _result(
                "UNVERIFIED",
                "judgment_blocker_not_confirmed",
                f"{why} Its blocking requirement is not a hiring condition or an experience "
                f"gap of at least {EXPERIENCE_GAP_INELIGIBLE_MONTHS} months.",
                row,
                "session_judgment",
            )
        return _result("INELIGIBLE", "session_judgment_ineligible", why, row, "session_judgment")
    if said == "ELIGIBLE":
        if status == "INELIGIBLE":
            return _result(
                "UNVERIFIED",
                "judgment_conflicts_with_score",
                f"{why} But the score rules say: {detail}",
                row,
                "session_judgment",
            )
        return _result(
            "ELIGIBLE",
            "session_judgment_eligible",
            f"{why} Score cross-check: {status}.",
            row,
            "session_judgment",
        )
    policy = _policy(row, judgment, status, why)
    if policy:
        return policy
    if status == "INELIGIBLE":
        return _result(status, code, detail, row, "session_judgment")
    return _result("UNVERIFIED", "session_judgment_unverified", why, row, "session_judgment")


def decide(row: dict) -> dict | None:
    """Decision for one ranking row, or None while it has no current assessment."""
    result = _decide(row)
    if result and result["status"] != "INELIGIBLE":
        result["detail"] += _scoped_note(row.get("findings", []))
    return result
