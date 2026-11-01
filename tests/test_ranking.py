"""Module 3B.2: deterministic local ranking and manual eligibility review.

Every job and assessment here is synthetic. Assessment rows are inserted directly,
with scores produced by the production score_resolved(), so the tests exercise ranking
without any API client, collector or network access.
"""

import json
import sqlite3

import pytest

from job_scraper import ranking, reviews
from job_scraper.candidate import CandidateProfile, ProfileEntry, profile_hash, profile_json
from job_scraper.cli import main
from job_scraper.config import MatchingConfig
from job_scraper.matching import (
    _job_input,
    _rule_hash,
    candidates,
    ensure_schema,
    latest_profile,
    score_resolved,
)
from job_scraper.models import Job
from job_scraper.processing import process_jobs
from job_scraper.storage import Store

CONFIG = MatchingConfig()
UNVERIFIED_TEXT = "Build Python APIs and deploy services. Fully remote."
ELIGIBLE_TEXT = (
    "2–3 years of software development experience required. Work from Pakistan. "
    "No employment restrictions."
)
INELIGIBLE_TEXT = "5+ years of professional experience required. Fully remote."
PROTECTED = (
    "jobs",
    "job_sources",
    "job_classifications",
    "job_classification_history",
    "candidate_profiles",
    "lifecycle_events",
    "ai_assessments",
)


def _job(source_id, *, description=UNVERIFIED_TEXT, source="remotive", title=None):
    return Job(
        source=source,
        source_id=source_id,
        company="Synthetic Co",
        title=title or f"Backend Python Engineer {source_id}",
        description=description,
        workplace_type="remote",
        source_url=f"https://example.com/jobs/{source_id}",
        attribution="Synthetic",
        attribution_url="https://example.com",
    )


def _items(*pairs):
    return [
        {
            "kind": kind,
            "text": f"{kind}{number}",
            "match_state": state,
            "job_evidence_id": f"R{number:03d}",
            "job_excerpt": f"excerpt {number}",
            "derived_fields": [],
            "skill_support_level": None,
            "profile_evidence_id": None,
            "candidate_skill_id": None,
        }
        for number, (kind, state) in enumerate(pairs, 1)
    ]


def _result(items, scoring="score-v5", mode="live"):
    return {
        **score_resolved(items, CONFIG),
        "referenced_findings": items,
        "requirement_classifications": [],
        "scoring_version": scoring,
        "evidence_version": "evidence-v8",
        "assessment_mode": mode,
    }


M, P, U, V = "MATCHED", "PARTIAL", "UNMATCHED", "UNVERIFIED"
STRONG = _items(("req", M), ("req", M), ("req", M), ("exp", M), ("duty", M), ("duty", M))
UNMET_EXPERIENCE = _items(("req", M), ("req", M), ("exp", U), ("duty", M), ("duty", M))
THIN = _items(("req", M), ("duty", M))
LIMITED = _items(("req", M), ("req", V), ("duty", M))
GAPS = _items(("req", U), ("req", U), ("req", U), ("req", U), ("exp", U), ("duty", V))
UNRESOLVED = _items(("req", V), ("req", V), ("duty", V))


@pytest.fixture
def store():
    with Store(":memory:") as opened:
        yield opened


def _approve(store):
    ensure_schema(store)
    profile = CandidateProfile(
        experience=[ProfileEntry(text="Built Python APIs professionally")],
        skills=["Python"],
        experience_years=1.25,
    )
    with store.db:
        store.db.execute(
            "INSERT INTO candidate_profiles(created_at,approved,profile_hash,profile) "
            "VALUES (?,?,?,?)",
            ("2026-11-02", 1, profile_hash(profile), profile_json(profile)),
        )


def _seed(store, jobs):
    for job in jobs:
        store.upsert(job)
    process_jobs(store)
    _approve(store)
    return {
        row["source_id"]: row["job_id"]
        for row in store.db.execute("SELECT source_id, job_id FROM job_sources")
    }


def _assess(store, job_id, items, *, rules=None, scoring="score-v5", mode="live", model="m"):
    candidate = candidates(store, include_history=True, job_id=job_id)[0]
    approved = latest_profile(store, approved_only=True)
    result = _result(items, scoring, mode)
    with store.db:
        return store.db.execute(
            """INSERT INTO ai_assessments
            (job_id,job_identity,profile_version,profile_hash,job_fingerprint,model,rule_hash,
             created_at,eligibility_status,score,result,token_usage)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job_id,
                json.dumps(candidate),
                approved["version"],
                approved["profile_hash"],
                _job_input(store, candidate)[0],
                model,
                rules or _rule_hash(CONFIG),
                "2026-11-02T00:00:00+00:00",
                candidate["eligibility_status"],
                result["score"],
                json.dumps(result),
                "{}",
            ),
        ).lastrowid


def _tables(store):
    return {
        name: store.db.execute(f"SELECT * FROM {name} ORDER BY rowid").fetchall()
        for name in PROTECTED
    }


def _ids(data):
    return [row["job_id"] for row in data["ranked"]]


def test_ranking_order_uses_interpretation_thin_flag_then_score(store):
    ids = _seed(store, [_job(name) for name in "abcdef"])
    for name, items in zip(
        "abcdef", [UNRESOLVED, GAPS, LIMITED, THIN, UNMET_EXPERIENCE, STRONG], strict=True
    ):
        _assess(store, ids[name], items)
    data = ranking.rank(store, CONFIG)
    # strong 100, strong 71, thin strong 100, limited 67, confirmed gaps 0, unresolved 0
    assert _ids(data) == [ids[x] for x in "fedcba"]
    assert [r["rank"] for r in data["ranked"]] == [1, 2, 3, 4, 5, 6]
    scores = {r["job_id"]: r["technical"]["score"] for r in data["ranked"]}
    assert scores[ids["f"]] == scores[ids["d"]] == 100 and scores[ids["e"]] == 71
    assert [r["technical"]["interpretation"] for r in data["ranked"]] == [
        "high_score_strong_evidence",
        "high_score_strong_evidence",
        "high_score_strong_evidence",
        "high_score_limited_evidence",
        "low_score_confirmed_gaps",
        "low_score_unresolved_evidence",
    ]
    assert ranking.rank(store, CONFIG) == data  # reproducible from the same stored data


def test_thin_perfect_score_is_visibly_distinguishable(store):
    ids = _seed(store, [_job("thin"), _job("solid")])
    _assess(store, ids["thin"], THIN)
    _assess(store, ids["solid"], STRONG)
    rows = {r["job_id"]: r for r in ranking.rank(store, CONFIG)["ranked"]}
    thin, solid = rows[ids["thin"]], rows[ids["solid"]]
    assert thin["technical"]["score"] == solid["technical"]["score"] == 100
    assert {"low_applicable_weight", "few_scored_findings"} <= set(thin["flags"])
    assert not {"low_applicable_weight", "few_scored_findings"} & set(solid["flags"])
    assert (thin["technical"]["applicable_weight"], thin["technical"]["scored_findings"]) == (60, 2)
    assert (solid["technical"]["applicable_weight"], solid["technical"]["scored_findings"]) == (
        85,
        6,
    )
    assert solid["rank"] < thin["rank"]


def test_high_score_with_low_coverage_is_flagged_and_outranked(store):
    ids = _seed(store, [_job("wide"), _job("blind")])
    _assess(store, ids["wide"], LIMITED)
    # A score can only come from determined findings, so low coverage means a low score.
    _assess(store, ids["blind"], _items(("req", M), ("req", V), ("req", V), ("duty", V)))
    rows = {r["job_id"]: r for r in ranking.rank(store, CONFIG)["ranked"]}
    blind = rows[ids["blind"]]
    assert blind["technical"]["score"] <= blind["technical"]["evidence_coverage"]
    assert "low_evidence_coverage" in blind["flags"]
    assert blind["technical"]["interpretation"] == "low_score_unresolved_evidence"
    assert rows[ids["wide"]]["rank"] < blind["rank"]


def test_confirmed_unmet_experience_is_flagged_and_explained(store):
    ids = _seed(store, [_job("exp")])
    _assess(store, ids["exp"], UNMET_EXPERIENCE)
    row = ranking.rank(store, CONFIG)["ranked"][0]
    assert "unmet_experience_minimum" in row["flags"]
    detail = ranking.explain(store, CONFIG, ids["exp"])
    assert [g["requirement_id"] for g in detail["confirmed_gaps"]] == ["R003"]
    assert detail["confirmed_gaps"][0]["reason"] == "approved_experience_below_minimum"
    assert detail["technical"]["counts"] == {
        "MATCHED": 4,
        "PARTIAL": 0,
        "UNMATCHED": 1,
        "UNVERIFIED": 0,
    }


def test_tie_breakers_prefer_eligible_then_lower_id(store):
    ids = _seed(
        store,
        [_job("b1"), _job("a1", description=ELIGIBLE_TEXT), _job("b2"), _job("b3")],
    )
    for name in ("b1", "a1", "b2", "b3"):
        _assess(store, ids[name], STRONG)
    data = ranking.rank(store, CONFIG)
    by_id = {r["job_id"]: r for r in data["ranked"]}
    assert by_id[ids["a1"]]["module2"]["status"] == "ELIGIBLE"
    assert _ids(data) == [ids["a1"], *sorted([ids["b1"], ids["b2"], ids["b3"]])]
    assert {by_id[ids[x]]["module2"]["status"] for x in ("b1", "b2", "b3")} == {"UNVERIFIED"}


def test_more_scored_findings_and_fewer_gaps_break_score_ties(store):
    ids = _seed(store, [_job("few"), _job("many")])
    # Both score 100, coverage 100, not thin; "many" has more scored findings.
    _assess(store, ids["few"], _items(("req", M), ("req", M), ("exp", M), ("duty", M)))
    _assess(store, ids["many"], STRONG)
    assert _ids(ranking.rank(store, CONFIG)) == [ids["many"], ids["few"]]


def test_current_stale_and_missing_assessments(store):
    ids = _seed(store, [_job("current"), _job("stale"), _job("missing")])
    _assess(store, ids["current"], STRONG)
    old = _assess(store, ids["stale"], STRONG, rules="0" * 64, scoring="score-v4")
    data = ranking.rank(store, CONFIG)
    assert data["counts"] == {
        "population": 3,
        "current": 1,
        "stale": 1,
        "awaiting_assessment": 1,
        "restricted_not_recommended": 0,
    }
    assert _ids(data) == [ids["current"]]  # stale scores never rank
    waiting = ranking.queue(store, CONFIG)
    assert [r["job_id"] for r in waiting["awaiting"]] == sorted([ids["stale"], ids["missing"]])
    by_id = {r["job_id"]: r for r in waiting["awaiting"]}
    assert by_id[ids["stale"]]["assessment_status"] == "stale"
    assert by_id[ids["stale"]]["assessment_history"][0]["stale_reasons"] == ["rules_changed"]
    assert by_id[ids["missing"]]["assessment_status"] == "missing"
    assert all("score" not in r and "technical" not in r for r in waiting["awaiting"])
    assert [r["job_id"] for r in ranking.queue(store, CONFIG, status="missing")["awaiting"]] == [
        ids["missing"]
    ]
    assert store.db.execute("SELECT id FROM ai_assessments WHERE id=?", (old,)).fetchone()


def test_job_content_change_makes_assessment_stale(store):
    ids = _seed(store, [_job("one")])
    _assess(store, ids["one"], STRONG)
    store.upsert(_job("one", description=UNVERIFIED_TEXT + " Added requirement."))
    process_jobs(store)
    data = ranking.rank(store, CONFIG)
    assert data["counts"]["current"] == 0 and data["counts"]["stale"] == 1
    assert "No current validated AI assessments" in data["empty_state"]
    history = ranking.queue(store, CONFIG)["awaiting"][0]["assessment_history"]
    assert history[0]["stale_reasons"] == ["job_or_screening_changed"]


def test_new_profile_makes_assessment_stale(store):
    ids = _seed(store, [_job("one")])
    _assess(store, ids["one"], STRONG)
    revised = CandidateProfile(skills=["Python", "SQL"], experience_years=1.25)
    with store.db:
        store.db.execute(
            "INSERT INTO candidate_profiles(created_at,approved,profile_hash,profile) "
            "VALUES (?,?,?,?)",
            ("2026-11-03", 1, profile_hash(revised), profile_json(revised)),
        )
    assert ranking.rank(store, CONFIG)["counts"]["stale"] == 1


def test_ineligible_jobs_are_excluded_and_unverified_is_not_promoted(store):
    ids = _seed(
        store,
        [
            _job("ok"),
            _job("elig", description=ELIGIBLE_TEXT),
            _job("bad", description=INELIGIBLE_TEXT),
        ],
    )
    for name in ("ok", "elig"):
        _assess(store, ids[name], STRONG)
    data = ranking.rank(store, CONFIG)
    status = {r["job_id"]: r["module2"]["status"] for r in data["ranked"]}
    assert status == {ids["ok"]: "UNVERIFIED", ids["elig"]: "ELIGIBLE"}
    assert ids["bad"] not in status
    assert ids["bad"] not in [r["job_id"] for r in ranking.queue(store, CONFIG)["awaiting"]]
    excluded = ranking.explain(store, CONFIG, ids["bad"])
    assert excluded["in_ranking_population"] is False
    assert excluded["reason"] == "module2_ineligible"
    assert [r["job_id"] for r in ranking.rank(store, CONFIG, eligibility="ELIGIBLE")["ranked"]] == [
        ids["elig"]
    ]


def test_stale_module2_classification_is_treated_as_unverified(store):
    ids = _seed(store, [_job("one", description=ELIGIBLE_TEXT)])
    assert ranking.queue(store, CONFIG)["awaiting"][0]["module2"]["status"] == "ELIGIBLE"
    store.upsert(_job("one", description=ELIGIBLE_TEXT + " Plus a new sentence."))
    row = ranking.queue(store, CONFIG)["awaiting"][0]
    assert row["job_id"] == ids["one"]
    assert row["module2"]["status"] == "UNVERIFIED" and row["module2"]["stale"] is True


def test_zero_one_and_many_assessments(store):
    empty = ranking.rank(store, CONFIG)
    assert empty["ranked"] == [] and "No relevant active jobs" in empty["empty_state"]
    ids = _seed(store, [_job("a"), _job("b"), _job("c")])
    none = ranking.rank(store, CONFIG)
    assert none["ranked"] == [] and "no ranking is produced" in none["empty_state"]
    assert none["counts"]["awaiting_assessment"] == 3
    assert len(ranking.queue(store, CONFIG)["awaiting"]) == 3
    _assess(store, ids["a"], STRONG)
    one = ranking.rank(store, CONFIG)
    assert _ids(one) == [ids["a"]] and one["empty_state"] is None
    _assess(store, ids["b"], THIN)
    _assess(store, ids["c"], GAPS)
    assert len(ranking.rank(store, CONFIG)["ranked"]) == 3
    assert (
        "no ranked job matches" in ranking.rank(store, CONFIG, min_score=101)["empty_state"].lower()
    )


def test_filters(store):
    ids = _seed(
        store,
        [
            _job("a", source="remotive"),
            _job("b", source="arbeitnow"),
            _job("c", source="remotive", description=ELIGIBLE_TEXT),
        ],
    )
    _assess(store, ids["a"], STRONG)
    _assess(store, ids["b"], LIMITED)
    _assess(store, ids["c"], GAPS)
    assert _ids(ranking.rank(store, CONFIG, confidence="medium")) == [ids["b"]]
    assert _ids(ranking.rank(store, CONFIG, min_score=60)) == [ids["a"], ids["b"]]
    assert _ids(ranking.rank(store, CONFIG, source="arbeitnow")) == [ids["b"]]
    assert _ids(ranking.rank(store, CONFIG, eligibility="ELIGIBLE")) == [ids["c"]]
    assert _ids(ranking.rank(store, CONFIG, limit=1)) == [ids["a"]]
    # Filtering never renumbers the global rank.
    assert ranking.rank(store, CONFIG, source="arbeitnow")["ranked"][0]["rank"] == 2
    assert ranking.rank(store, CONFIG, model="other-model")["counts"]["current"] == 0


def test_historical_jobs_are_labeled_and_never_recommended(store):
    ids = _seed(store, [_job("active"), _job("closed")])
    store.db.execute("UPDATE job_sources SET lifecycle_state='closed' WHERE source_id='closed'")
    store.db.commit()
    assert ranking.rank(store, CONFIG)["counts"]["population"] == 1
    _assess(store, ids["active"], THIN)
    _assess(store, ids["closed"], STRONG)
    data = ranking.rank(store, CONFIG, include_history=True)
    assert _ids(data) == [ids["active"], ids["closed"]]
    assert [r["recommended"] for r in data["ranked"]] == [True, False]
    assert data["ranked"][1]["lifecycle"] == "closed"
    assert "[historical, not recommended]" in ranking.render_rank(data)


def test_high_score_with_unverified_eligibility_needs_review_not_promotion(store):
    ids = _seed(store, [_job("one")])
    _assess(store, ids["one"], STRONG)
    row = ranking.rank(store, CONFIG)["ranked"][0]
    assert "eligibility_review_pending" in row["flags"] and row["module2"]["status"] == "UNVERIFIED"
    reviews.add_review(
        store,
        ids["one"],
        "evidence_supported",
        "Careers page lists Pakistan",
        "https://example.com/p",
    )
    row = ranking.rank(store, CONFIG)["ranked"][0]
    assert "eligibility_review_pending" not in row["flags"]
    # The automated status is untouched; the manual observation is a separate field.
    assert row["module2"]["status"] == "UNVERIFIED"
    assert row["manual_review"]["status"] == "evidence_supported"
    assert row["manual_review"]["source"] == "manual_review"
    stored = json.loads(store.db.execute("SELECT result FROM job_classifications").fetchone()[0])
    assert stored["status"] == "UNVERIFIED"


def test_confirmed_restricted_is_excluded_from_the_recommended_ranking(store):
    ids = _seed(store, [_job("one"), _job("two")])
    first = _assess(store, ids["one"], STRONG)
    _assess(store, ids["two"], THIN)
    reviews.add_review(
        store, ids["one"], "confirmed_restricted", "US residents only", "Careers page: US only"
    )
    before = _tables(store)
    data = ranking.rank(store, CONFIG)
    # Only the unrestricted job is recommended; the restricted job is kept, unranked, below.
    assert _ids(data) == [ids["two"]] and data["ranked"][0]["rank"] == 1
    held = data["restricted_not_recommended"]
    assert [r["job_id"] for r in held] == [ids["one"]]
    assert held[0]["rank"] is None and held[0]["recommendation"] == (
        "not_recommended_manual_restriction"
    )
    assert held[0]["technical"]["score"] == 100  # the score is kept, not deleted or zeroed
    assert "manual_review_confirmed_restricted" in held[0]["flags"]
    assert held[0]["module2"]["status"] == "UNVERIFIED"  # automated status untouched
    assert data["counts"]["restricted_not_recommended"] == 1 and data["counts"]["current"] == 1
    text = ranking.render_rank(data)
    assert text.index("Manually confirmed restricted - NOT recommended") > text.index("  1. [")
    # Explicit access: hide the section, show only restricted jobs, or explain the job.
    assert ranking.rank(store, CONFIG, restricted="hide")["restricted_not_recommended"] == []
    only = ranking.rank(store, CONFIG, restricted="only")
    assert only["ranked"] == [] and [r["job_id"] for r in only["restricted_not_recommended"]] == [
        ids["one"]
    ]
    detail = ranking.explain(store, CONFIG, ids["one"])
    assert detail["rank"] is None and detail["assessment_id"] == first
    assert detail["recommendation"] == "not_recommended_manual_restriction"
    assert "recommendation: not_recommended_manual_restriction" in ranking.render_explain(detail)
    # Nothing was deleted or rewritten by looking.
    assert _tables(store) == before
    assert reviews.history(store, ids["one"])[0]["status"] == "confirmed_restricted"


def test_restricted_job_is_not_queued_for_assessment_and_returns_when_review_changes(store):
    ids = _seed(store, [_job("held"), _job("open")])
    reviews.add_review(store, ids["held"], "confirmed_restricted", "Terms exclude PK", "Terms 4.2")
    waiting = ranking.queue(store, CONFIG)
    assert [r["job_id"] for r in waiting["awaiting"]] == [ids["open"]]
    assert waiting["restricted_not_queued"] == 1
    assert "1 manually confirmed-restricted job(s)" in ranking.render_queue(waiting)
    counts = ranking.rank(store, CONFIG)["counts"]
    assert (counts["awaiting_assessment"], counts["restricted_not_recommended"]) == (1, 1)
    # A newer review supersedes it; the restriction ends and the job rejoins the queue.
    reviews.add_review(store, ids["held"], "unresolved", "Employer page changed; unclear now")
    assert [r["job_id"] for r in ranking.queue(store, CONFIG)["awaiting"]] == sorted(
        [ids["held"], ids["open"]]
    )
    assert [e["status"] for e in reviews.history(store, ids["held"])] == [
        "confirmed_restricted",
        "unresolved",
    ]


def test_restriction_only_counts_when_it_is_the_newest_review(store):
    ids = _seed(store, [_job("one")])
    _assess(store, ids["one"], STRONG)
    reviews.add_review(store, ids["one"], "confirmed_restricted", "No PK hiring", "Terms 1")
    assert ranking.rank(store, CONFIG)["ranked"] == []
    reviews.add_review(store, ids["one"], "evidence_supported", "Updated terms allow PK", "Terms 2")
    data = ranking.rank(store, CONFIG)
    assert _ids(data) == [ids["one"]] and data["restricted_not_recommended"] == []
    assert data["ranked"][0]["module2"]["status"] == "UNVERIFIED"


def test_explanation_distinguishes_gaps_from_missing_information(store):
    ids = _seed(store, [_job("one")])
    items = _items(("req", U), ("req", V), ("req", M), ("exp", U), ("duty", V), ("eligibility", V))
    items[1]["skill_support_level"] = "listed"
    items[1]["profile_evidence_id"] = "P-0123456789ab"
    _assess(store, ids["one"], items)
    detail = ranking.explain(store, CONFIG, ids["one"])
    assert [g["requirement_id"] for g in detail["confirmed_gaps"]] == ["R001", "R004"]
    assert detail["confirmed_gaps"][0]["reason"] == "skill_absent_from_approved_profile"
    info = detail["insufficient_information"]
    assert [i["reason"] for i in info["unresolved_requirements"]] == ["listed_on_profile_only"]
    assert info["unresolved_requirements"][0]["profile_evidence_id"] == "P-0123456789ab"
    assert [i["requirement_id"] for i in info["unresolved_duties"]] == ["R005"]
    assert [i["reason"] for i in info["hiring_conditions"]] == ["hiring_condition_not_scored"]
    assert [s["requirement_id"] for s in detail["strengths"]] == ["R003"]
    assert detail["application_url"] is None and "salary" not in detail
    assert detail["module2"]["status"] == "UNVERIFIED" and "country" not in detail
    assert set(detail["technical"]["components"]) == {
        "required_skills",
        "experience",
        "role_alignment",
        "preferred_projects",
    }


def test_explanation_skips_findings_excluded_from_score(store):
    ids = _seed(store, [_job("one")])
    result = _result(STRONG)
    result["excluded_findings"] = [
        {"index": 0, "kind": "req", "reason": "duplicate_skill_requirement"}
    ]
    candidate = candidates(store, job_id=ids["one"])[0]
    approved = latest_profile(store, approved_only=True)
    with store.db:
        store.db.execute(
            """INSERT INTO ai_assessments
            (job_id,job_identity,profile_version,profile_hash,job_fingerprint,model,rule_hash,
             created_at,eligibility_status,score,result,token_usage)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ids["one"],
                "{}",
                approved["version"],
                approved["profile_hash"],
                _job_input(store, candidate)[0],
                "m",
                _rule_hash(CONFIG),
                "2026-11-02",
                "UNVERIFIED",
                100,
                json.dumps(result),
                "{}",
            ),
        )
    detail = ranking.explain(store, CONFIG, ids["one"])
    assert "R001" not in [s["requirement_id"] for s in detail["strengths"]]
    assert detail["excluded_from_score"][0]["reason"] == "duplicate_skill_requirement"


def test_ranking_is_read_only_and_never_recomputes_scores(store):
    ids = _seed(store, [_job("a"), _job("b")])
    _assess(store, ids["a"], STRONG)
    _assess(store, ids["b"], THIN, rules="0" * 64, scoring="score-v4")
    before = _tables(store)
    ranking.rank(store, CONFIG)
    ranking.queue(store, CONFIG)
    ranking.explain(store, CONFIG, ids["a"])
    assert _tables(store) == before
    assert not hasattr(ranking, "score_resolved") and not hasattr(ranking, "genai")
    saved = json.loads(
        store.db.execute(
            "SELECT result FROM ai_assessments WHERE job_id=?", (ids["a"],)
        ).fetchone()[0]
    )
    assert ranking.rank(store, CONFIG)["ranked"][0]["technical"]["score"] == saved["score"]


def test_manual_review_history_is_append_only_and_separate(store):
    ids = _seed(store, [_job("one")])
    before = _tables(store)
    first = reviews.add_review(store, ids["one"], "unresolved", "Posting is silent on Pakistan")
    second = reviews.add_review(
        store,
        ids["one"],
        "evidence_supported",
        "Employer page lists Pakistan",
        "https://x.test/jobs",
    )
    third = reviews.add_review(
        store,
        ids["one"],
        "confirmed_restricted",
        "Terms exclude Pakistan",
        "Terms 4.2: not available in PK",
    )
    history = reviews.history(store, ids["one"])
    assert [e["id"] for e in history] == [first["id"], second["id"], third["id"]]
    assert [e["status"] for e in history] == [
        "unresolved",
        "evidence_supported",
        "confirmed_restricted",
    ]
    assert reviews.latest(store)[ids["one"]]["status"] == "confirmed_restricted"
    assert history[0]["evidence"] is None and history[0]["module2_status_at_review"] == "UNVERIFIED"
    assert history[0]["source"] == "manual_review"
    # Module 2, its history, and every assessment table are untouched.
    assert _tables(store) == before
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store.db.execute("UPDATE eligibility_reviews SET status='unresolved'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store.db.execute("DELETE FROM eligibility_reviews")
    assert len(reviews.history(store)) == 3


def test_manual_review_requires_evidence_and_valid_input(store):
    ids = _seed(store, [_job("one")])
    for status in ("evidence_supported", "confirmed_restricted"):
        with pytest.raises(ValueError, match="evidence is required"):
            reviews.add_review(store, ids["one"], status, "I believe this is fine")
        with pytest.raises(ValueError, match="evidence is required"):
            reviews.add_review(store, ids["one"], status, "I believe this", "   ")
    with pytest.raises(ValueError, match="explanation is required"):
        reviews.add_review(store, ids["one"], "unresolved", "  ")
    with pytest.raises(ValueError, match="status must be"):
        reviews.add_review(store, ids["one"], "ELIGIBLE", "Looks good", "evidence")
    with pytest.raises(ValueError, match="No job with id"):
        reviews.add_review(store, 9999, "unresolved", "Nothing")
    with pytest.raises(ValueError, match="exceeds"):
        reviews.add_review(store, ids["one"], "unresolved", "x" * 2001)
    assert reviews.history(store) == []
    # The table itself also rejects an unsupported claim written around the API.
    reviews.ensure_schema(store)
    with pytest.raises(sqlite3.IntegrityError):
        store.db.execute(
            "INSERT INTO eligibility_reviews(job_id,status,explanation,module2_status,reviewed_at)"
            " VALUES (1,'evidence_supported','claim','UNVERIFIED','now')"
        )


def test_unsupported_country_claim_never_becomes_module2_eligibility(store):
    ids = _seed(store, [_job("one", description="Build Python APIs. Candidates from any country.")])
    _assess(store, ids["one"], STRONG)
    reviews.add_review(store, ids["one"], "unresolved", "Employer claims 'any country'; unverified")
    row = ranking.explain(store, CONFIG, ids["one"])
    assert row["module2"]["status"] == "UNVERIFIED"
    assert row["manual_review"]["status"] == "unresolved"
    assert "eligibility_review_pending" in row["flags"]
    dimensions = row["module2"]["dimensions"]
    assert dimensions["geography"] == "UNVERIFIED" and dimensions["restrictions"] == "UNVERIFIED"
    assert row["manual_review"]["evidence"] is None


def test_review_schema_is_created_with_a_verified_backup(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    with Store(path) as opened:
        ids = _seed(opened, [_job("one")])
        assert reviews.history(opened) == [] and not reviews.has_schema(opened)
        reviews.add_review(opened, ids["one"], "unresolved", "Needs a look")
        assert reviews.has_schema(opened)
    backups = list((tmp_path / "backups").glob("jobs-pre-reviews-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as copy:
        assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not copy.execute(
            "SELECT 1 FROM sqlite_master WHERE name='eligibility_reviews'"
        ).fetchone()
        assert copy.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1


def _config(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        'database = "jobs.sqlite3"\n[[sources]]\nsource = "remotive"\n', encoding="utf-8"
    )
    return str(config)


def _cli(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_cli_rank_review_workflow(tmp_path, capsys):
    config = _config(tmp_path)
    code, out, _ = _cli(capsys, "rank", "list", "--config", config)
    assert code == 0 and "No relevant active jobs" in out
    with Store(tmp_path / "jobs.sqlite3") as opened:
        ids = _seed(opened, [_job("a"), _job("b")])
        _assess(opened, ids["a"], STRONG)
    code, out, _ = _cli(capsys, "rank", "list", "--config", config)
    assert code == 0
    assert "1. [1] Backend Python Engineer a" in out and "score 100/100" in out
    assert "not a hiring probability" in out
    code, out, _ = _cli(capsys, "rank", "list", "--json", "--min-score", "50", "--config", config)
    data = json.loads(out)
    assert data["counts"]["current"] == 1 and data["ranked"][0]["rank"] == 1
    code, out, _ = _cli(capsys, "rank", "queue", "--json", "--config", config)
    assert [r["job_id"] for r in json.loads(out)["awaiting"]] == [2]
    code, out, _ = _cli(capsys, "rank", "explain", "--job-id", "1", "--config", config)
    assert code == 0 and "Confirmed gaps (poor suitability):" in out and "Rank 1" in out
    assert _cli(capsys, "rank", "explain", "--config", config)[0] == 2
    code, out, _ = _cli(capsys, "review", "list", "--config", config)
    assert code == 0 and "No manual eligibility reviews" in out
    assert not (tmp_path / "backups").exists() or not list(
        (tmp_path / "backups").glob("jobs-pre-reviews-*")
    )
    code, _, err = _cli(
        capsys,
        "review",
        "add",
        "--job-id",
        "1",
        "--status",
        "evidence_supported",
        "--explanation",
        "No proof",
        "--config",
        config,
    )
    assert code == 2 and "evidence is required" in err
    code, out, _ = _cli(
        capsys,
        "review",
        "add",
        "--job-id",
        "1",
        "--status",
        "evidence_supported",
        "--explanation",
        "Careers page lists Pakistan",
        "--evidence",
        "https://x.test/p",
        "--config",
        config,
    )
    assert code == 0 and "evidence_supported" in out
    code, out, _ = _cli(
        capsys,
        "review",
        "add",
        "--job-id",
        "1",
        "--status",
        "unresolved",
        "--explanation",
        "Page changed; recheck",
        "--config",
        config,
    )
    code, out, _ = _cli(capsys, "review", "show", "--job-id", "1", "--config", config)
    assert out.count("#") == 2 and "evidence_supported" in out and "unresolved" in out
    code, out, _ = _cli(capsys, "review", "list", "--json", "--config", config)
    assert [e["status"] for e in json.loads(out)] == ["unresolved"]
    code, out, _ = _cli(capsys, "rank", "explain", "--job-id", "1", "--config", config)
    assert "Manual review history:" in out and "https://x.test/p" in out
    assert "not verified by the application" in out
    assert "Module 2 UNVERIFIED" in out


def test_job22_style_history_is_reused_not_recomputed(store, monkeypatch):
    ids = _seed(store, [_job("job22")])
    old = _assess(
        store, ids["job22"], _items(("req", V), ("duty", V)), rules="0" * 64, scoring="score-v4"
    )
    current = _assess(store, ids["job22"], GAPS, mode="offline_rescore")
    before = _tables(store)
    monkeypatch.setattr("job_scraper.matching.score_resolved", lambda *a, **k: 1 / 0)
    detail = ranking.explain(store, CONFIG, ids["job22"])
    assert detail["assessment_id"] == current and detail["assessment_mode"] == "offline_rescore"
    history = {a["id"]: a for a in detail["assessment_history"]}
    assert history[old]["current"] is False and history[old]["stale_reasons"] == ["rules_changed"]
    assert history[current]["current"] is True
    assert _tables(store) == before


def _changed(source_id="one", extra=" Now also requires onsite days."):
    return _job(source_id, description=UNVERIFIED_TEXT + extra)


def test_review_records_the_live_job_fingerprint_not_a_stale_classification(store):
    ids = _seed(store, [_job("one")])
    store.upsert(_changed())  # content changed; Module 2 has not been re-run
    entry = reviews.add_review(store, ids["one"], "unresolved", "Reviewed the new text")
    assert entry["classification_fingerprint"] == reviews.fingerprints(store)[ids["one"]]
    stored = store.db.execute("SELECT fingerprint FROM job_classifications").fetchone()[0]
    assert entry["classification_fingerprint"] != stored  # the classification is stale
    assert entry["currency"] == "current"


def test_outdated_assertions_become_needs_reverification_without_rewriting_rows(store):
    ids = _seed(store, [_job("one")])
    _assess(store, ids["one"], STRONG)
    reviews.add_review(store, ids["one"], "evidence_supported", "Careers page lists PK", "page")
    assert reviews.latest(store)[ids["one"]]["effective_status"] == "evidence_supported"
    stored = store.db.execute("SELECT * FROM eligibility_reviews").fetchall()
    store.upsert(_changed())
    entry = reviews.latest(store)[ids["one"]]
    assert entry["status"] == "evidence_supported"  # the recorded claim is unchanged
    assert entry["effective_status"] == "needs_reverification"
    assert (entry["currency"], entry["currency_reason"]) == (
        "needs_reverification",
        "job_content_changed",
    )
    assert store.db.execute("SELECT * FROM eligibility_reviews").fetchall() == stored
    assert reviews.label(entry) == (
        f"evidence_supported (#{entry['id']}, needs_reverification: job_content_changed)"
    )


def test_outdated_evidence_claim_no_longer_clears_the_eligibility_review_flag(store):
    ids = _seed(store, [_job("one")])
    _assess(store, ids["one"], STRONG)
    reviews.add_review(store, ids["one"], "evidence_supported", "Careers page lists PK", "page")
    assert "eligibility_review_pending" not in ranking.rank(store, CONFIG)["ranked"][0]["flags"]
    store.upsert(_changed())
    process_jobs(store)
    _approve_check = _assess(store, ids["one"], STRONG)  # a fresh assessment of the new text
    row = ranking.rank(store, CONFIG)["ranked"][0]
    assert row["assessment_id"] == _approve_check
    assert {"manual_review_needs_reverification", "eligibility_review_pending"} <= set(row["flags"])
    assert row["module2"]["status"] == "UNVERIFIED"
    assert "needs_reverification: job_content_changed" in ranking.render_rank(
        ranking.rank(store, CONFIG)
    )


def test_outdated_restriction_is_not_carried_onto_changed_job_details(store):
    ids = _seed(store, [_job("one")])
    _assess(store, ids["one"], STRONG)
    reviews.add_review(store, ids["one"], "confirmed_restricted", "US only", "Careers page")
    assert ranking.rank(store, CONFIG)["restricted_not_recommended"][0]["job_id"] == ids["one"]
    store.upsert(_changed(extra=" Open to candidates worldwide."))
    process_jobs(store)
    data = ranking.rank(store, CONFIG)
    assert (
        data["restricted_not_recommended"] == []
        and data["counts"]["restricted_not_recommended"] == 0
    )
    # The old assessment is stale too, so the job awaits a current one, with the old review shown.
    waiting = ranking.queue(store, CONFIG)["awaiting"]
    assert [r["job_id"] for r in waiting] == [ids["one"]]
    assert waiting[0]["recommendation"] == "awaiting_current_assessment"
    assert waiting[0]["manual_review"]["effective_status"] == "needs_reverification"
    assert "needs_reverification: job_content_changed" in ranking.render_queue(
        ranking.queue(store, CONFIG)
    )
    _assess(store, ids["one"], STRONG)
    row = ranking.rank(store, CONFIG)["ranked"][0]
    assert row["recommendation"] == "recommended"
    assert "manual_review_confirmed_restricted" not in row["flags"]
    assert "manual_review_needs_reverification" in row["flags"]


def test_stale_module2_classification_still_detects_an_outdated_review(store):
    ids = _seed(store, [_job("one", description=ELIGIBLE_TEXT)])
    reviews.add_review(store, ids["one"], "evidence_supported", "Page lists PK", "page")
    store.upsert(_job("one", description=ELIGIBLE_TEXT + " Added clause."))  # not re-processed
    row = ranking.queue(store, CONFIG)["awaiting"][0]
    assert row["module2"]["stale"] is True and row["module2"]["status"] == "UNVERIFIED"
    assert row["manual_review"]["currency_reason"] == "job_content_changed"


def test_review_is_restored_when_the_job_content_returns(store):
    ids = _seed(store, [_job("one")])
    _assess(store, ids["one"], STRONG)
    reviews.add_review(store, ids["one"], "confirmed_restricted", "US only", "Careers page")
    store.upsert(_changed())
    assert reviews.latest(store)[ids["one"]]["effective_status"] == "needs_reverification"
    store.upsert(_job("one"))  # the posting reverts to the reviewed text
    process_jobs(store)
    entry = reviews.latest(store)[ids["one"]]
    assert (entry["currency"], entry["effective_status"]) == ("current", "confirmed_restricted")
    assert ranking.rank(store, CONFIG)["restricted_not_recommended"][0]["job_id"] == ids["one"]


def test_a_new_review_makes_the_job_current_again_and_history_is_preserved(store):
    ids = _seed(store, [_job("one")])
    reviews.add_review(store, ids["one"], "confirmed_restricted", "US only", "Careers page")
    store.upsert(_changed())
    renewed = reviews.add_review(
        store, ids["one"], "confirmed_restricted", "Still US only", "Page 2"
    )
    assert (renewed["currency"], renewed["effective_status"]) == ("current", "confirmed_restricted")
    history = reviews.history(store, ids["one"])
    assert [(e["id"], e["currency"], e["superseded"]) for e in history] == [
        (1, "needs_reverification", True),
        (2, "current", False),
    ]
    assert history[0]["explanation"] == "US only"  # the original entry is preserved verbatim


def test_a_review_without_a_recorded_fingerprint_needs_reverification(store):
    ids = _seed(store, [_job("one")])
    reviews.ensure_schema(store)
    with store.db:
        store.db.execute(
            "INSERT INTO eligibility_reviews(job_id,status,explanation,evidence,module2_status,"
            "classification_fingerprint,reviewed_at) VALUES (?,?,?,?,?,NULL,?)",
            (ids["one"], "confirmed_restricted", "Old entry", "page", "UNVERIFIED", "2026-11-01"),
        )
    entry = reviews.latest(store)[ids["one"]]
    assert entry["currency_reason"] == "no_fingerprint_recorded"
    assert entry["effective_status"] == "needs_reverification"
    assert not ranking.queue(store, CONFIG)["restricted_not_queued"]


def test_unresolved_reviews_make_no_claim_when_outdated(store):
    ids = _seed(store, [_job("one")])
    reviews.add_review(store, ids["one"], "unresolved", "Needs a look")
    store.upsert(_changed())
    entry = reviews.latest(store)[ids["one"]]
    assert entry["currency"] == "needs_reverification" and entry["effective_status"] == "unresolved"


def test_evidence_is_a_user_claim_and_is_never_fetched_or_verified(store):
    for name in ("httpx", "urllib", "socket", "requests"):
        assert name not in vars(reviews) and name not in vars(ranking)
    ids = _seed(store, [_job("one")])
    entry = reviews.add_review(
        store, ids["one"], "evidence_supported", "Page lists PK", "https://no-such-host.invalid/x"
    )
    assert entry["evidence"] == "https://no-such-host.invalid/x"
    assert entry["verification"] == reviews.VERIFICATION_NOTE
    assert "did not verify" in entry["verification"]
    assert reviews.add_review(store, ids["one"], "unresolved", "Unclear")["verification"] == "none"
    assert "not verified by the application" in ranking.render_reviews(
        reviews.history(store, ids["one"])
    )


def test_zero_one_and_many_assessed_jobs_with_restrictions(store):
    ids = _seed(store, [_job("a"), _job("b"), _job("c")])
    reviews.add_review(store, ids["a"], "confirmed_restricted", "No PK", "Terms")
    # Zero assessments: the restricted job is not queued and not recommended.
    zero = ranking.rank(store, CONFIG)
    assert zero["ranked"] == [] and zero["restricted_not_recommended"] == []
    assert "No current validated AI assessments" in zero["empty_state"]
    # One assessment, and it belongs to the restricted job: nothing is recommended.
    _assess(store, ids["a"], STRONG)
    one = ranking.rank(store, CONFIG)
    assert one["ranked"] == [] and len(one["restricted_not_recommended"]) == 1
    assert one["empty_state"].startswith("No job is recommended")
    # Many: restricted jobs stay separate and unnumbered.
    _assess(store, ids["b"], STRONG)
    _assess(store, ids["c"], GAPS)
    many = ranking.rank(store, CONFIG)
    assert _ids(many) == [ids["b"], ids["c"]]
    assert [r["rank"] for r in many["ranked"]] == [1, 2]
    assert many["counts"]["current"] == 2 and many["counts"]["restricted_not_recommended"] == 1
    assert ranking.rank(store, CONFIG, restricted="only")["ranked"] == []
    assert (
        "No manually confirmed-restricted job matches."
        in ranking.rank(store, CONFIG, restricted="only", min_score=101)["empty_state"]
    )


def test_scores_assessments_and_history_survive_restriction_and_reverification(store):
    ids = _seed(store, [_job("job22")])
    old = _assess(store, ids["job22"], _items(("req", V), ("duty", V)), rules="0" * 64)
    current = _assess(store, ids["job22"], GAPS, mode="offline_rescore")
    before = _tables(store)
    reviews.add_review(store, ids["job22"], "confirmed_restricted", "US only", "Careers page")
    held = ranking.rank(store, CONFIG)["restricted_not_recommended"][0]
    assert held["assessment_id"] == current and held["module2"]["status"] == "UNVERIFIED"
    store.upsert(_changed("job22"))
    after = _tables(store)
    assert after["ai_assessments"] == before["ai_assessments"]
    assert after["job_classifications"] == before["job_classifications"]
    assert after["job_classification_history"] == before["job_classification_history"]
    assert store.db.execute("SELECT count(*) FROM ai_assessments").fetchone()[0] == 2
    assert {old, current} == {r[0] for r in store.db.execute("SELECT id FROM ai_assessments")}


def test_cli_shows_current_and_outdated_reviews_consistently(tmp_path, capsys):
    config = _config(tmp_path)
    with Store(tmp_path / "jobs.sqlite3") as opened:
        ids = _seed(opened, [_job("a"), _job("b")])
        _assess(opened, ids["a"], STRONG)
        _assess(opened, ids["b"], THIN)
    base = ["--config", config]
    _cli(
        capsys,
        "review",
        "add",
        "--job-id",
        "1",
        "--status",
        "confirmed_restricted",
        "--explanation",
        "US only",
        "--evidence",
        "Careers page",
        *base,
    )
    _cli(
        capsys,
        "review",
        "add",
        "--job-id",
        "2",
        "--status",
        "evidence_supported",
        "--explanation",
        "Lists PK",
        "--evidence",
        "Careers page",
        *base,
    )
    _, out, _ = _cli(capsys, "rank", "list", *base)
    assert "Manually confirmed restricted - NOT recommended" in out
    assert "confirmed_restricted (#1, current)" in out and "evidence_supported (#2, current)" in out
    _, out, _ = _cli(capsys, "rank", "list", "--hide-restricted", *base)
    assert "NOT recommended" not in out
    _, out, _ = _cli(capsys, "rank", "list", "--only-restricted", "--json", *base)
    assert [r["job_id"] for r in json.loads(out)["restricted_not_recommended"]] == [1]
    assert _cli(capsys, "review", "list", "--outdated", *base)[1].strip() == (
        "No manual eligibility reviews have been recorded."
    )
    with Store(tmp_path / "jobs.sqlite3") as opened:
        opened.upsert(_changed("a"))
        opened.upsert(_changed("b"))
    outdated = "needs_reverification: job_content_changed"
    for command in (
        ("review", "list"),
        ("review", "show", "--job-id", "1"),
        ("rank", "explain", "--job-id", "1"),
        ("rank", "explain", "--job-id", "2"),
        ("rank", "queue"),
    ):
        _, out, _ = _cli(capsys, *command, *base)
        assert outdated in out, command
    _, out, _ = _cli(capsys, "review", "list", "--outdated", "--json", *base)
    assert [(e["job_id"], e["effective_status"]) for e in json.loads(out)] == [
        (1, "needs_reverification"),
        (2, "needs_reverification"),
    ]
    _, out, _ = _cli(capsys, "rank", "queue", "--json", *base)
    assert {r["job_id"] for r in json.loads(out)["awaiting"]} == {1, 2}
    _cli(
        capsys,
        "review",
        "add",
        "--job-id",
        "1",
        "--status",
        "confirmed_restricted",
        "--explanation",
        "Rechecked: still US only",
        "--evidence",
        "Careers page 2",
        *base,
    )
    _, out, _ = _cli(capsys, "review", "show", "--job-id", "1", *base)
    assert "[needs_reverification: job_content_changed, superseded]" in out
    assert "#3 job 1" in out and "[current]" in out
    _, out, _ = _cli(capsys, "review", "list", "--outdated", *base)
    assert "job 2" in out and "job 1" not in out
