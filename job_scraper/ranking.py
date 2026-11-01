"""Deterministic, explainable local ranking of current AI assessments (Module 3B.2).

Reads stored data only: no Gemini request, collection or network access, and no
new scoring or eligibility logic. A technical score is evidence fit with the
approved profile; it is not a hiring probability and never changes Module 2.
Sort order (ascending key, so the first row is the best):

1. recommended jobs (active, listed) before historical jobs
2. interpretation group: strong-evidence high, limited-evidence high,
   confirmed-gap low, unresolved-evidence low
3. well-supported before thin postings
4. higher normalized score
5. higher evidence coverage
6. more scored findings
7. fewer confirmed qualification gaps
8. Module 2 ELIGIBLE before UNVERIFIED
9. lower job id
"""

import json

from . import reviews
from .experience import compare, requirement_in_context
from .matching import HIGH_SCORE, _job_input, _rule_hash, candidates, latest_profile
from .processing import inspect_results

INTERPRETATION_ORDER = {
    "high_score_strong_evidence": 0,
    "high_score_limited_evidence": 1,
    "low_score_confirmed_gaps": 2,
    "low_score_unresolved_evidence": 3,
}
ELIGIBILITY_ORDER = {"ELIGIBLE": 0, "UNVERIFIED": 1}
THIN_APPLICABLE_WEIGHT = 60
THIN_MIN_SCORED_FINDINGS = 4
LOW_EVIDENCE_COVERAGE = 50
EXCERPT_CHARS = 200
SEMANTICS = (
    "Technical evidence fit with the approved profile, from stored assessments. It is not "
    "a hiring probability or employment eligibility. Module 2 eligibility and manual "
    "reviews are shown separately and never combined with the score."
)
STATE_KEYS = ("MATCHED", "PARTIAL", "UNMATCHED", "UNVERIFIED")


def _trim(text, size=EXCERPT_CHARS):
    text = " ".join((text or "").split())
    return text if len(text) <= size else text[: size - 1] + "..."


def _reason(item) -> str:
    fields = item.get("derived_fields", [])
    if item["match_state"] == "UNMATCHED":
        if item["kind"] == "exp":
            return (
                "technology_years_below_minimum"
                if "professional_years_below_technology_minimum" in fields
                else "approved_experience_below_minimum"
            )
        return "skill_absent_from_approved_profile"
    if item["kind"] == "eligibility":
        return "hiring_condition_not_scored"
    if "technology_specific_years_not_established" in fields:
        return "technology_specific_years_not_established"
    if item.get("skill_support_level") == "listed":
        return "listed_on_profile_only"
    if "alternative_requirement_unresolved" in fields:
        return "alternative_requirement_unresolved"
    if "ambiguous_skill_requirement" in fields:
        return "requirement_too_broad_to_verify"
    if item["kind"] == "unresolved":
        return "requirement_wording_unresolved"
    return "no_supporting_profile_evidence"


def _experience_view(item, approved_months) -> dict | None:
    """Required versus approved experience in years and months, for an experience finding."""
    stored = item.get("experience_comparison")
    if stored:
        return stored
    requirement = requirement_in_context(item["text"], item.get("job_excerpt") or "")
    if requirement is None:
        return None  # a vague wording is not given a number
    return compare(requirement, approved_months)


def _finding(item, approved_months=None) -> dict:
    view = {
        "requirement_id": item["job_evidence_id"],
        "kind": item["kind"],
        "text": _trim(item["text"]),
        "state": item["match_state"],
        "support": item.get("skill_support_level"),
        "reason": _reason(item),
        "job_excerpt": _trim(item.get("job_excerpt")),
        "profile_evidence_id": item.get("profile_evidence_id"),
        "candidate_skill_id": item.get("candidate_skill_id"),
    }
    if "alternative_options" in item:
        view["options"] = item["alternative_options"]
    if item["kind"] == "exp":
        view["experience"] = _experience_view(item, approved_months)
        if (
            item["match_state"] == "UNMATCHED"
            and (view["experience"] or {}).get("requirement_kind") == "preferred"
        ):
            view["reason"] = "approved_experience_below_preferred_level"
    return view


def _technical(result) -> dict:
    breakdown, confidence = result["breakdown"], result["confidence"]
    scored = sum(part["total"] for part in breakdown.values())
    return {
        "score": result["score"],
        "raw_score": result["raw_score"],
        "applicable_weight": result["applicable_weight"],
        "scored_findings": scored,
        "evidence_coverage": confidence["evidence_coverage"],
        "confidence_level": confidence["level"],
        "score_ceiling": confidence["score_ceiling"],
        "confirmed_shortfall": confidence["confirmed_shortfall"],
        "interpretation": confidence["interpretation"],
        "counts": {state: confidence["counts"].get(state, 0) for state in STATE_KEYS},
        "components": {
            name: {
                key: part[key]
                for key in (
                    "status",
                    "weight",
                    "points",
                    "matched",
                    "partial",
                    "unmatched",
                    "unverified",
                    "total",
                )
            }
            for name, part in breakdown.items()
        },
    }


def _flags(row) -> list[str]:
    flags = []
    technical = row["technical"]
    if technical["applicable_weight"] <= THIN_APPLICABLE_WEIGHT:
        flags.append("low_applicable_weight")
    if technical["scored_findings"] < THIN_MIN_SCORED_FINDINGS:
        flags.append("few_scored_findings")
    if technical["evidence_coverage"] < LOW_EVIDENCE_COVERAGE:
        flags.append("low_evidence_coverage")
    parts = technical["components"]
    if parts["preferred_projects"]["status"] == "stated_not_extracted":
        flags.append("preferences_not_extracted")
    if parts["experience"]["unmatched"]:
        flags.append("unmet_experience_minimum")
    manual = row["manual_review"] or {}
    effective = manual.get("effective_status")
    if effective == "confirmed_restricted":
        flags.append("manual_review_confirmed_restricted")
    if effective == "needs_reverification":
        flags.append("manual_review_needs_reverification")
    # Only a current, evidence-citing review counts as reviewed; an outdated claim does not.
    reviewed = effective in {"evidence_supported", "confirmed_restricted"}
    if row["module2"]["status"] != "ELIGIBLE" and technical["score"] >= HIGH_SCORE and not reviewed:
        flags.append("eligibility_review_pending")
    return flags


def _sort_key(row):
    technical = row["technical"]
    thin = bool({"low_applicable_weight", "few_scored_findings"} & set(row["flags"]))
    return (
        0 if row["recommended"] else 1,
        INTERPRETATION_ORDER.get(technical["interpretation"], len(INTERPRETATION_ORDER)),
        1 if thin else 0,
        -technical["score"],
        -technical["evidence_coverage"],
        -technical["scored_findings"],
        technical["counts"]["UNMATCHED"],
        ELIGIBILITY_ORDER.get(row["module2"]["status"], len(ELIGIBILITY_ORDER)),
        row["job_id"],
    )


def _module2(entry) -> dict:
    classification = (entry or {}).get("classification") or {}
    return {
        "status": (entry or {}).get("status", "UNVERIFIED"),
        "stale": bool((entry or {}).get("stale", True)),
        "rules_version": (entry or {}).get("rules_version"),
        "dimensions": {
            name: part["status"]
            for name, part in classification.get("merged_decisions", {}).items()
        },
        "source_conflict": bool(classification.get("source_conflict", False)),
    }


def _assess(rows, approved, fingerprint, rules, model):
    """Newest current assessment (or None) and a summary of every stored assessment."""
    current, summaries = None, []
    for row in rows:
        result = json.loads(row["result"])
        reasons = []
        if not approved or row["profile_hash"] != approved["profile_hash"]:
            reasons.append("profile_changed")
        if row["job_fingerprint"] != fingerprint:
            reasons.append("job_or_screening_changed")
        if row["rule_hash"] != rules:
            reasons.append("rules_changed")
        if model and row["model"] != model:
            reasons.append("different_model")
        summaries.append(
            {
                "id": row["id"],
                "mode": result.get("assessment_mode", "live"),
                "scoring_version": result.get("scoring_version"),
                "evidence_version": result.get("evidence_version"),
                "model": row["model"],
                "score": row["score"],
                "created_at": row["created_at"],
                "current": not reasons,
                "stale_reasons": reasons,
            }
        )
        if not reasons and current is None:
            current = (row, result)
    return current, summaries


def build(store, config, *, include_history=False, model=None, job_id=None) -> list[dict]:
    """One entry per relevant job in the ranking population, current or not."""
    queue = candidates(store, include_history=include_history, job_id=job_id)
    approved = latest_profile(store, approved_only=True)
    rules = _rule_hash(config)
    screening = {r["job_id"]: r for r in inspect_results(store, include_history=include_history)}
    jobs = {r["id"]: r for r in store.list_jobs(include_history)}
    sources: dict[int, list[dict]] = {}
    for r in store.db.execute("SELECT job_id,source,board FROM job_sources ORDER BY rowid"):
        sources.setdefault(r[0], []).append({"source": r[1], "board": r[2]})
    stored: dict[int, list] = {}
    for r in store.db.execute("SELECT * FROM ai_assessments ORDER BY id DESC"):
        stored.setdefault(r["job_id"], []).append(r)
    manual = reviews.latest(store, reviews.fingerprints(store))
    output = []
    for item in queue:
        identity = item["job_id"]
        fingerprint = _job_input(store, item)[0]
        current, summaries = _assess(stored.get(identity, []), approved, fingerprint, rules, model)
        job = jobs[identity]
        row = {
            "job_id": identity,
            "title": job["title"],
            "company": job["company"],
            "sources": sources.get(identity, []),
            "source_url": job["source_url"],
            "application_url": job["application_url"],
            "lifecycle": job["status"],
            "recommended": not item["historical"],
            "module2": _module2(screening.get(identity)),
            "manual_review": manual.get(identity),
            "restricted": (manual.get(identity) or {}).get("effective_status")
            == "confirmed_restricted",
            "assessment_status": "current" if current else "stale" if summaries else "missing",
            "assessment_history": summaries,
            "approved_experience": approved["experience"] if approved else None,
            "technical": None,
            "assessment_id": None,
            "assessment_mode": None,
            "findings": [],
            "excluded": [],
        }
        if current:
            saved, result = current
            row["technical"] = _technical(result)
            row["assessment_id"] = saved["id"]
            row["assessment_mode"] = result.get("assessment_mode", "live")
            row["findings"] = result["referenced_findings"]
            row["excluded"] = result.get("excluded_findings", [])
            row["flags"] = _flags(row)
        else:
            row["flags"] = []
        row["recommendation"] = (
            "not_recommended_manual_restriction"
            if row["restricted"]
            else "historical_not_recommended"
            if not row["recommended"]
            else "recommended"
            if current
            else "awaiting_current_assessment"
        )
        output.append(row)
    return output


def _matches(row, *, eligibility, confidence, min_score, source):
    if eligibility and row["module2"]["status"] != eligibility:
        return False
    technical = row["technical"]
    if confidence and technical["confidence_level"] != confidence:
        return False
    if min_score is not None and technical["score"] < min_score:
        return False
    if source and source not in {s["source"] for s in row["sources"]}:
        return False
    return True


def _public(row) -> dict:
    """List view: summary without the finding-level detail."""
    return {key: value for key, value in row.items() if key not in {"findings", "excluded"}}


def rank(
    store,
    config,
    *,
    include_history=False,
    model=None,
    eligibility=None,
    confidence=None,
    min_score=None,
    source=None,
    restricted="show",
    limit=None,
) -> dict:
    """Recommended ranking, plus a separate non-recommended section for current restrictions.

    `restricted` is show (default: separate section), hide or only. A job whose newest
    manual review is a current, evidence-backed confirmed_restricted is never ranked and
    receives no rank number; its score, assessments and reviews are kept and shown.
    """
    population = build(store, config, include_history=include_history, model=model)
    held = [r for r in population if r["restricted"]]
    open_rows = [r for r in population if not r["restricted"]]
    current = sorted((r for r in open_rows if r["assessment_status"] == "current"), key=_sort_key)
    for position, row in enumerate(current, 1):
        row["rank"] = position
    held_current = sorted((r for r in held if r["assessment_status"] == "current"), key=_sort_key)
    for row in held_current:
        row["rank"] = None

    def keep(row):
        return _matches(
            row, eligibility=eligibility, confidence=confidence, min_score=min_score, source=source
        )

    shown = [r for r in current if keep(r)] if restricted != "only" else []
    held_shown = [r for r in held_current if keep(r)] if restricted != "hide" else []
    counts = {
        "population": len(population),
        "current": len(current),
        "stale": sum(r["assessment_status"] == "stale" for r in open_rows),
        "awaiting_assessment": sum(r["assessment_status"] == "missing" for r in open_rows),
        "restricted_not_recommended": len(held),
    }
    if limit:
        shown, held_shown = shown[:limit], held_shown[:limit]
    empty = None
    waiting = counts["stale"] + counts["awaiting_assessment"]
    if not population:
        empty = "No relevant active jobs are available to rank."
    elif restricted == "only":
        if not held_shown:
            empty = "No manually confirmed-restricted job matches."
    elif not current and not held_current and waiting:
        empty = (
            f"No current validated AI assessments exist, so no ranking is produced. "
            f"{waiting} relevant job(s) await a current assessment; see `rank queue`."
        )
    elif not current:
        empty = (
            "No job is recommended: every job with a current assessment is manually "
            f"confirmed restricted ({len(held)} listed separately), and {waiting} more await "
            "a current assessment."
        )
    elif not shown:
        empty = "No ranked job matches the requested filters."
    approved = latest_profile(store, approved_only=True)
    return {
        "semantics": SEMANTICS,
        "approved_experience": approved["experience"] if approved else None,
        "counts": counts,
        "empty_state": empty,
        "ranked": [_public(r) for r in shown],
        "restricted_not_recommended": [_public(r) for r in held_shown],
    }


def queue(
    store, config, *, include_history=False, model=None, status=None, source=None, limit=None
) -> dict:
    """Relevant jobs without a current assessment, in a separate queue: never scored as zero."""
    population = build(store, config, include_history=include_history, model=model)
    waiting = [
        r
        for r in population
        if r["assessment_status"] != "current"
        and not r["restricted"]
        and (status is None or r["assessment_status"] == status)
        and (source is None or source in {s["source"] for s in r["sources"]})
    ]
    waiting.sort(
        key=lambda r: (
            0 if r["recommended"] else 1,
            ELIGIBILITY_ORDER.get(r["module2"]["status"], len(ELIGIBILITY_ORDER)),
            r["job_id"],
        )
    )
    if limit:
        waiting = waiting[:limit]
    return {
        "semantics": "These jobs have no current validated assessment, so they have no score.",
        "count": len(waiting),
        # Confirmed-restricted jobs are not queued: assessing them would spend money on
        # a job you have evidence you cannot take. Reverify a review to put one back.
        "restricted_not_queued": sum(
            r["restricted"] and r["assessment_status"] != "current" for r in population
        ),
        "next_step": (
            "`match rescore` re-scores compatible saved classifications offline; otherwise "
            "`match analyze --live` sends job text to Gemini and is billable (needs your "
            "authorization)."
        ),
        "awaiting": [
            {
                key: r[key]
                for key in (
                    "job_id",
                    "title",
                    "company",
                    "sources",
                    "application_url",
                    "lifecycle",
                    "recommended",
                    "module2",
                    "assessment_status",
                    "assessment_history",
                    "manual_review",
                    "recommendation",
                )
            }
            for r in waiting
        ],
    }


def explain(store, config, job_id, *, include_history=True, model=None) -> dict:
    """Everything behind one job's position, or why it is outside the ranking population."""
    population = build(store, config, include_history=include_history, model=model)
    ranked = sorted(
        (r for r in population if r["assessment_status"] == "current" and not r["restricted"]),
        key=_sort_key,
    )
    positions = {r["job_id"]: n for n, r in enumerate(ranked, 1)}
    row = next((r for r in population if r["job_id"] == job_id), None)
    if row is None:
        return _excluded(store, job_id)
    detail = {key: value for key, value in row.items() if key not in {"findings", "excluded"}}
    skipped = {entry["index"] for entry in row["excluded"]}
    findings = [item for index, item in enumerate(row["findings"]) if index not in skipped]
    months = (row["approved_experience"] or {}).get("months")
    # Findings the score left out stay visible, an experience level with its required,
    # approved and gap figures, so a preferred or maximum level is not silently dropped.
    detail["excluded_from_score"] = [
        {**entry, "experience": _experience_view(row["findings"][entry["index"]], months)}
        if entry.get("kind") == "exp"
        else entry
        for entry in row["excluded"]
    ]
    detail["rank"] = positions.get(job_id)
    detail["semantics"] = SEMANTICS
    detail["manual_review_history"] = reviews.history(store, job_id)
    detail["confirmed_gaps"] = []
    detail["insufficient_information"] = {
        "unresolved_requirements": [],
        "unresolved_duties": [],
        "hiring_conditions": [],
    }
    for item in findings:
        view = _finding(item, months)
        if item["match_state"] == "UNMATCHED":
            detail["confirmed_gaps"].append(view)
        elif item["kind"] == "eligibility":
            detail["insufficient_information"]["hiring_conditions"].append(view)
        elif item["match_state"] == "UNVERIFIED" and item["kind"] == "duty":
            detail["insufficient_information"]["unresolved_duties"].append(view)
        elif item["match_state"] == "UNVERIFIED":
            detail["insufficient_information"]["unresolved_requirements"].append(view)
    if row["technical"]:
        detail["strengths"] = [
            _finding(i, months) for i in findings if i["match_state"] in {"MATCHED", "PARTIAL"}
        ]
    detail["notes"] = {
        "poor_suitability": "confirmed_gaps are determined shortfalls against the approved profile.",
        "insufficient_information": (
            "unresolved items earned no points but are not shown to be unmet; hiring "
            "conditions are not scored and never decide eligibility."
        ),
    }
    return detail


def _excluded(store, job_id) -> dict:
    job = next((j for j in store.list_jobs(True) if j["id"] == job_id), None)
    if job is None:
        raise ValueError(f"No job with id {job_id}")
    screened = inspect_results(store, job_id=job_id, include_history=True)
    status = screened[0]["status"] if screened else "UNVERIFIED"
    if job["status"] != "active" or job["is_listed"] != 1:
        reason = "not_active_listed_job"
    elif status == "INELIGIBLE":
        reason = "module2_ineligible"
    else:
        reason = "not_relevant_or_no_description"
    return {
        "job_id": job_id,
        "title": job["title"],
        "company": job["company"],
        "in_ranking_population": False,
        "reason": reason,
        "lifecycle": job["status"],
        "module2": _module2(screened[0] if screened else None),
        "manual_review_history": reviews.history(store, job_id),
    }


def _experience_text(summary) -> str:
    if not summary or summary.get("months") is None:
        return "not established"
    source = (
        "converted from legacy decimal years"
        if summary.get("source") == "experience_years"
        else "approved in months"
    )
    note = f"; {summary['note']}" if summary.get("note") else ""
    return f"{summary['display']} ({summary['months']} months; {source}{note})"


def _gap_text(view) -> str:
    """Required, approved and gap for an experience finding, in years and months."""
    if not view:
        return ""
    approved = view["approved_display"] or "not established"
    gap = view["gap_display"]
    scope = (
        " [years in a named technology; total experience cannot settle it]"
        if (view.get("scope") == "technology")
        else ""
    )
    shortfall = "gap not computed" if gap is None else f"gap {gap}"
    kind = "" if view["requirement_kind"] == "minimum" else f" ({view['requirement_kind']})"
    return f" (required {view['required_display']}{kind}; approved {approved}; {shortfall}){scope}"


def _line(row) -> list[str]:
    head = f"[{row['job_id']}] {row['title']} - {row['company']}"
    names = sorted({s["source"] for s in row["sources"]})
    return [f"{head} ({', '.join(names) or 'unknown source'})"]


def _manual_text(row) -> str:
    return reviews.label(row["manual_review"])


def _review_line(entry) -> str:
    state = (
        "current"
        if entry["currency"] == "current"
        else f"needs_reverification: {entry['currency_reason']}"
    )
    extra = ", superseded" if entry["superseded"] else ""
    text = (
        f"#{entry['id']} job {entry['job_id']} {entry['reviewed_at']} {entry['status']} "
        f"[{state}{extra}] (Module 2 at review: {entry['module2_status_at_review']}): "
        f"{entry['explanation']}"
    )
    if entry["evidence"]:
        text += f" | evidence (user-supplied, not verified by the application): {entry['evidence']}"
    return text


def _row_lines(row, number) -> list[str]:
    technical = row["technical"]
    marker = "" if row["recommended"] else "  [historical, not recommended]"
    lines = ["", f"{number:>3}. " + _line(row)[0] + marker]
    lines.append(
        f"     score {technical['score']}/100 | confidence {technical['confidence_level']} "
        f"(coverage {technical['evidence_coverage']}%, ceiling {technical['score_ceiling']}) "
        f"| applicable weight {technical['applicable_weight']} | "
        f"{technical['scored_findings']} scored findings"
    )
    counts = technical["counts"]
    lines.append(
        f"     matched {counts['MATCHED']} | partial {counts['PARTIAL']} | "
        f"unmatched {counts['UNMATCHED']} | unverified {counts['UNVERIFIED']}"
    )
    lines.append(f"     {technical['interpretation']}")
    lines.append(
        f"     Module 2: {row['module2']['status']}"
        f"{' (stale)' if row['module2']['stale'] else ''} | manual review: "
        f"{_manual_text(row)} | assessment #{row['assessment_id']} ({row['assessment_mode']})"
    )
    if row["flags"]:
        lines.append("     flags: " + ", ".join(row["flags"]))
    return lines


def render_rank(data) -> str:
    counts = data["counts"]
    lines = [
        data["semantics"],
        f"Approved experience: {_experience_text(data.get('approved_experience'))}",
        f"Population {counts['population']} | current assessments {counts['current']} | "
        f"stale {counts['stale']} | awaiting assessment {counts['awaiting_assessment']} | "
        f"manually restricted {counts['restricted_not_recommended']}",
    ]
    if data["empty_state"]:
        lines += ["", data["empty_state"]]
    for row in data["ranked"]:
        lines += _row_lines(row, row["rank"])
    if data["restricted_not_recommended"]:
        lines += [
            "",
            "== Manually confirmed restricted - NOT recommended (never ranked; scores, "
            "assessments and reviews are kept) ==",
        ]
        for row in data["restricted_not_recommended"]:
            lines += _row_lines(row, "-")
    return "\n".join(lines)


def render_queue(data) -> str:
    lines = [data["semantics"], f"{data['count']} job(s) awaiting a current assessment", ""]
    for row in data["awaiting"]:
        old = len(row["assessment_history"])
        lines.append(
            f"[{row['job_id']}] {row['title']} - {row['company']} | "
            f"{row['assessment_status']} | Module 2 {row['module2']['status']}"
            f"{'' if row['recommended'] else ' | historical'}"
            f"{f' | {old} old assessment(s)' if old else ''}"
            f" | manual review: {_manual_text(row)}"
        )
    if data["restricted_not_queued"]:
        lines += [
            "",
            f"{data['restricted_not_queued']} manually confirmed-restricted job(s) without an "
            "assessment are not queued.",
        ]
    lines += ["", data["next_step"]]
    return "\n".join(lines)


def render_explain(data) -> str:
    if not data.get("in_ranking_population", True):
        return (
            f"[{data['job_id']}] {data['title']} - {data['company']}\n"
            f"Not in the ranking population: {data['reason']} "
            f"(lifecycle {data['lifecycle']}, Module 2 {data['module2']['status']})."
        )
    lines = [
        data["semantics"],
        "",
        _line(data)[0],
        f"Lifecycle {data['lifecycle']} | Module 2 {data['module2']['status']}"
        f"{' (stale)' if data['module2']['stale'] else ''} | manual review: "
        f"{_manual_text(data)}",
        f"Approved experience: {_experience_text(data.get('approved_experience'))}",
        f"Application URL: {data['application_url'] or 'not provided'}",
        f"Source URL: {data['source_url']}",
        f"Assessment: {data['assessment_status']} | recommendation: {data['recommendation']}",
    ]
    technical = data["technical"]
    if technical:
        lines += [
            f"Rank {data['rank'] if data['rank'] else 'none (not ranked)'} | score {technical['score']}/100 (raw {technical['raw_score']}) "
            f"| applicable weight {technical['applicable_weight']} | "
            f"{technical['scored_findings']} scored findings",
            f"Confidence {technical['confidence_level']} | coverage "
            f"{technical['evidence_coverage']}% | ceiling {technical['score_ceiling']} | "
            f"{technical['interpretation']}",
        ]
        for name, part in technical["components"].items():
            lines.append(
                f"  {name}: {part['status']} | weight {part['weight']} | points "
                f"{part['points']:.1f} | M{part['matched']} P{part['partial']} "
                f"U{part['unmatched']} V{part['unverified']}"
            )
        if data["flags"]:
            lines.append("Flags: " + ", ".join(data["flags"]))
        lines.append("Confirmed gaps (poor suitability):")
        lines += [
            f"  {g['requirement_id']} [{g['state']}] {g['text']} - {g['reason']}"
            f"{_gap_text(g.get('experience'))}"
            for g in data["confirmed_gaps"]
        ] or ["  none"]
        lines.append("Insufficient information:")
        for name, items in data["insufficient_information"].items():
            lines.append(f"  {name}: {len(items)}")
            lines += [
                f"    {i['requirement_id']} {i['text']} - {i['reason']}"
                f"{_gap_text(i.get('experience'))}"
                for i in items
            ]
    if data.get("excluded_from_score"):
        lines.append("Not scored:")
        lines += [
            f"  {e.get('job_evidence_id', '')} {e['text']} - {e['reason']}"
            f"{_gap_text(e.get('experience'))}"
            for e in data["excluded_from_score"]
        ]
    lines.append("Assessments:")
    lines += [
        f"  #{a['id']} {a['mode']} {a['scoring_version']} score {a['score']} "
        f"{'current' if a['current'] else 'stale: ' + ', '.join(a['stale_reasons'])}"
        for a in data["assessment_history"]
    ] or ["  none"]
    lines.append("Manual review history:")
    lines += ["  " + _review_line(e) for e in data["manual_review_history"]] or ["  none"]
    return "\n".join(lines)


def render_reviews(entries) -> str:
    if not entries:
        return "No manual eligibility reviews have been recorded."
    return "\n".join(_review_line(e) for e in entries)
