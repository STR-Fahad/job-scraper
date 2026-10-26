"""Independent remote-only and cross-source eligibility regressions."""

import json

import pytest
from test_processing import candidate

from job_scraper.processing import inspect_results, process_jobs
from job_scraper.screening import classify
from job_scraper.storage import Store


@pytest.mark.parametrize(
    "workplace,expected",
    [
        ("remote", "ELIGIBLE"),
        ("hybrid", "INELIGIBLE"),
        ("onsite", "INELIGIBLE"),
        ("unknown", "UNVERIFIED"),
    ],
)
def test_remote_only_preference(workplace, expected):
    result = classify(candidate(workplace_type=workplace))
    assert result.workplace_type == workplace
    assert result.decisions["workplace"].status == expected
    assert result.status == expected
    assert result.decisions["workplace"].evidence[0].quote == workplace


@pytest.mark.parametrize(
    "attendance",
    [
        "You must work from the office every Tuesday.",
        "Office attendance is required.",
        "Expected to be on-site for meetings.",
        "3 days per week in the office.",
        "Work from the office 2 days per week.",
        "Hybrid schedule required.",
        "You must commute to our office.",
        "This position requires you to work in the office.",
        "This is a hybrid role with 2 days in the office each week.",
        "Required to come into the office on Thursdays.",
        "This role requires office attendance.",
        "The role requires occasional office visits.",
        "In-office presence is mandatory.",
    ],
)
def test_mandatory_office_overrides_remote_provider(attendance):
    result = classify(
        candidate(
            description="2 years experience. Work from Pakistan. "
            "No employment restrictions. " + attendance
        )
    )
    assert result.decisions["workplace"].status == "INELIGIBLE"
    assert result.status == "INELIGIBLE"
    assert any(item.quote == attendance for item in result.decisions["workplace"].evidence)


@pytest.mark.parametrize(
    "description",
    [
        "No office attendance is required.",
        "Optional office visits are available.",
        "Remote-first; in-person collaboration is encouraged.",
        "Office attendance is not required.",
    ],
)
def test_optional_office_language_does_not_reject_remote(description):
    result = classify(
        candidate(
            description="2 years experience. Work from Pakistan. "
            "No employment restrictions. " + description
        )
    )
    assert result.decisions["workplace"].status == "ELIGIBLE"


def test_unknown_provider_with_mandatory_office_is_ineligible():
    result = classify(
        candidate(
            workplace_type="unknown",
            description=(
                "2 years experience. Work from Pakistan. No employment restrictions. "
                "Office attendance is mandatory."
            ),
        )
    )
    assert result.decisions["workplace"].status == "INELIGIBLE"


def test_opposing_office_attendance_statements_remain_unverified():
    result = classify(
        candidate(
            description=(
                "2 years experience. Work from Pakistan. No employment restrictions. "
                "Office attendance is mandatory. No office attendance is required."
            )
        )
    )
    assert result.decisions["workplace"].status == "UNVERIFIED"
    assert result.decisions["workplace"].conflict
    assert result.status == "UNVERIFIED"
    assert len(result.decisions["workplace"].evidence) == 3


def _sources(store, first, second):
    store.upsert(candidate(**first))
    store.upsert(candidate(source="ashby", board="another", source_id="other", **second))
    assert store.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    assert store.db.execute("SELECT count(*) FROM job_sources").fetchone()[0] == 2
    process_jobs(store)
    return inspect_results(store)[0]["classification"]


def test_cross_source_exclusion_beats_missing_geography():
    with Store(":memory:") as store:
        result = _sources(
            store,
            {
                "description": "2 years experience. No employment restrictions.",
                "restrictions": {"candidate_required_location": "United States"},
            },
            {"description": "2 years experience. No employment restrictions.", "restrictions": {}},
        )
        assert result["status"] == "INELIGIBLE"
        assert result["merged_decisions"]["geography"]["status"] == "INELIGIBLE"
        assert not result["merged_decisions"]["geography"]["conflict"]
        assert [v["decisions"]["geography"]["status"] for v in result["variants"]] == [
            "UNVERIFIED",
            "INELIGIBLE",
        ] or [v["decisions"]["geography"]["status"] for v in result["variants"]] == [
            "INELIGIBLE",
            "UNVERIFIED",
        ]
        evidence = [
            e["quote"] for v in result["variants"] for e in v["decisions"]["geography"]["evidence"]
        ]
        assert '"United States"' in " ".join(evidence)
        assert {v["source"] for v in result["variants"]} == {"ashby", "greenhouse"}


def test_cross_source_opposing_geography_remains_unverified():
    with Store(":memory:") as store:
        result = _sources(
            store,
            {
                "description": "2 years experience. No employment restrictions.",
                "restrictions": {"candidate_required_location": "United States"},
            },
            {
                "description": "2 years experience. No employment restrictions.",
                "restrictions": {"candidate_required_location": "Pakistan"},
            },
        )
        assert result["status"] == "UNVERIFIED"
        assert result["merged_decisions"]["geography"]["conflict"]
        assert result["source_conflict"]


def test_internal_contradiction_is_not_erased_by_other_source_exclusion():
    with Store(":memory:") as store:
        result = _sources(
            store,
            {
                "description": "2 years experience. Work from Pakistan. No employment restrictions.",
                "restrictions": {"candidate_required_location": "United States"},
            },
            {
                "description": "2 years experience. No employment restrictions.",
                "restrictions": {"candidate_required_location": "United States"},
            },
        )
        assert result["merged_decisions"]["geography"]["status"] == "UNVERIFIED"
        assert result["merged_decisions"]["geography"]["conflict"]
        assert result["status"] == "UNVERIFIED"


def test_cross_source_workplace_unknown_does_not_weaken_hybrid():
    with Store(":memory:") as store:
        result = _sources(store, {"workplace_type": "hybrid"}, {"workplace_type": "unknown"})
        assert result["status"] == "INELIGIBLE"
        assert result["merged_decisions"]["workplace"]["status"] == "INELIGIBLE"


def test_reprocessing_preserves_old_version_and_source_payload(tmp_path):
    with Store(tmp_path / "jobs.sqlite3") as store:
        store.upsert(candidate(workplace_type="hybrid", raw={"provider_flag": "hybrid"}))
        original = [tuple(row) for row in store.db.execute("SELECT * FROM job_sources")]
        process_jobs(store)
        old = tuple(store.db.execute("SELECT * FROM job_classifications").fetchone())
        store.db.execute("UPDATE job_classifications SET rules_version='prior-version'")
        store.db.commit()
        result = process_jobs(store)
        assert result["processed"] == 1 and result["INELIGIBLE"] == 1
        history = [
            tuple(row)
            for row in store.db.execute(
                "SELECT job_id,rules_version,fingerprint,processed_at,result FROM job_classification_history"
            )
        ]
        assert old in history
        assert (
            json.loads(history[-1][-1])["variants"][0]["decisions"]["workplace"]["status"]
            == "INELIGIBLE"
        )
        assert original == [tuple(row) for row in store.db.execute("SELECT * FROM job_sources")]
        assert store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []
