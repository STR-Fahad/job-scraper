import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from test_storage import job

from job_scraper.cli import main
from job_scraper.models import Statistics
from job_scraper.processing import classify, inspect_results, process_jobs
from job_scraper.storage import Store


def candidate(**changes):
    data = dict(
        title="Backend Python Engineer",
        workplace_type="remote",
        description="2–3 years of software development experience required. Work from Pakistan. No employment restrictions.",
    )
    data.update(changes)
    return job(**data)


@pytest.mark.parametrize(
    "title,category",
    [
        ("Backend Engineer", "backend"),
        ("Full Stack Developer", "full-stack"),
        ("Ruby on Rails Developer", "Rails"),
        ("Python Developer", "Python"),
        ("Machine Learning Engineer", "AI/ML"),
        ("LLM Engineer", "LLM"),
        ("Data Engineer", "data engineering"),
        ("DevOps Engineer", "DevOps"),
    ],
)
def test_engineering_categories(title, category):
    result = classify(candidate(title=title))
    assert category in result.categories
    assert result.decisions["category"].status == "ELIGIBLE"
    assert result.decisions["category"].evidence


def test_full_positive_and_ambiguous_role():
    assert classify(candidate()).status == "ELIGIBLE"
    result = classify(candidate(title="Sales Manager", description="We sell Python tools."))
    assert result.status == "UNVERIFIED"
    assert result.decisions["category"].status == "UNVERIFIED"


@pytest.mark.parametrize(
    "description,state",
    [
        ("0-3 years of experience required", "ELIGIBLE"),
        ("2 to 5 years of Python experience", "ELIGIBLE"),
        ("3+ years experience", "ELIGIBLE"),
        ("4+ years of experience required", "INELIGIBLE"),
        ("At least 5 years of professional experience", "INELIGIBLE"),
        ("24 months of experience", "ELIGIBLE"),
        ("No prior experience required", "ELIGIBLE"),
        ("5 years experience preferred", "UNVERIFIED"),
        ("5 years experience is not required", "UNVERIFIED"),
        ("5 years experience or equivalent skills", "UNVERIFIED"),
        ("Our company has 20 years of history", "UNVERIFIED"),
        ("Senior developer", "UNVERIFIED"),
        ("2 years experience required; 5 years Python experience preferred", "ELIGIBLE"),
        ("2 years Python experience; 5 years professional experience", "INELIGIBLE"),
    ],
)
def test_experience(description, state):
    assert classify(candidate(description=description)).decisions["experience"].status == state


@pytest.mark.parametrize("workplace", ["remote", "hybrid", "onsite", "unknown"])
def test_arrangement_is_preserved(workplace):
    result = classify(candidate(workplace_type=workplace, description=""))
    assert result.workplace_type == workplace
    assert result.decisions["geography"].status == "UNVERIFIED"
    assert result.status == "UNVERIFIED"


@pytest.mark.parametrize(
    "restrictions,state",
    [
        ({"candidate_required_location": "Worldwide"}, "ELIGIBLE"),
        ({"candidate_required_location": "Pakistan"}, "ELIGIBLE"),
        ({"candidate_required_location": "United States"}, "INELIGIBLE"),
        ({"candidate_required_location": "EMEA"}, "UNVERIFIED"),
        ({"candidate_required_location": "Worldwide except Pakistan"}, "UNVERIFIED"),
        ({"locationRestrictions": [{"name": "Pakistan", "alpha2": "PK"}]}, "ELIGIBLE"),
        ({"locationRestrictions": [{"name": "Canada", "alpha2": "CA"}]}, "INELIGIBLE"),
        ({"locationRestrictions": []}, "UNVERIFIED"),
        (
            {"candidate_required_location": "Worldwide", "timezoneRestrictions": ["UTC-5"]},
            "UNVERIFIED",
        ),
    ],
)
def test_structured_geography(restrictions, state):
    result = classify(candidate(description="", restrictions=restrictions))
    assert result.decisions["geography"].status == state


@pytest.mark.parametrize(
    "description,state",
    [
        ("Work from Pakistan", "ELIGIBLE"),
        ("Work from anywhere in the world", "ELIGIBLE"),
        ("Must be based in United States", "INELIGIBLE"),
        ("US-only", "INELIGIBLE"),
        ("We cannot hire Pakistan", "INELIGIBLE"),
        ("We have offices in Pakistan", "UNVERIFIED"),
        ("Our customers work from Pakistan", "UNVERIFIED"),
        ("Remote worldwide except certain countries", "UNVERIFIED"),
    ],
)
def test_description_geography(description, state):
    assert classify(candidate(description=description)).decisions["geography"].status == state


def test_conflicting_location_and_attendance():
    result = classify(
        candidate(
            description="Must be based in United States",
            restrictions={"candidate_required_location": "Worldwide"},
        )
    )
    assert result.decisions["geography"].status == "UNVERIFIED"
    assert len(result.decisions["geography"].evidence) == 2
    assert (
        classify(candidate(description="", workplace_type="hybrid", location=["Berlin, Germany"]))
        .decisions["geography"]
        .status
        == "INELIGIBLE"
    )
    assert (
        classify(candidate(description="", workplace_type="remote", location=["Berlin, Germany"]))
        .decisions["geography"]
        .status
        == "UNVERIFIED"
    )


def test_company_experience_and_unknown_optional_conditions():
    assert (
        classify(candidate(description="Our team has 20 years of experience"))
        .decisions["experience"]
        .status
        == "UNVERIFIED"
    )
    assert (
        classify(candidate(description="2 years experience or 5 years experience"))
        .decisions["experience"]
        .status
        == "UNVERIFIED"
    )
    result = classify(
        candidate(restrictions={"candidate_required_location": "Worldwide; excluding Pakistan"})
    )
    assert result.decisions["geography"].status == "UNVERIFIED"


def test_preferred_section_does_not_become_required_experience():
    result = classify(
        candidate(
            description="Requirements:\n2 years experience\nNice to have:\n5 years experience"
        )
    )
    assert result.decisions["experience"].status == "ELIGIBLE"
    assert result.experience_requirements[-1]["optional"]
    result = classify(
        candidate(
            description="Preferred qualifications:\n5 years experience\nRequired qualifications:\n4 years experience"
        )
    )
    assert result.decisions["experience"].status == "INELIGIBLE"
    result = classify(
        candidate(description="Work from Pakistan. Overlap with US business hours required.")
    )
    assert result.decisions["geography"].status == "UNVERIFIED"


@pytest.mark.parametrize(
    "restriction",
    [
        "US citizenship required",
        "Must be a permanent resident",
        "No visa sponsorship available",
        "Must be authorized to work in the US",
        "Security clearance required",
        "Contractor-only",
        "Employees only",
        "Sponsorship available",
        "",
    ],
)
def test_legal_restrictions_never_assume_candidate_citizenship(restriction):
    result = classify(
        candidate(description="2 years experience. Work from Pakistan. " + restriction)
    )
    assert result.decisions["restrictions"].status == "UNVERIFIED"
    assert result.status == "UNVERIFIED"
    if restriction:
        assert result.decisions["restrictions"].evidence


def test_storage_backup_idempotence_and_original_records(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    with Store(path) as store:
        store.upsert(candidate(raw={"untouched": True}))
        before = {
            name: [tuple(r) for r in store.db.execute(f"SELECT * FROM {name}")]
            for name in ("jobs", "job_sources", "lifecycle_events")
        }
        assert process_jobs(store)["processed"] == 1
        first = tuple(store.db.execute("SELECT * FROM job_classifications").fetchone())
        assert process_jobs(store)["unchanged"] == 1
        assert tuple(store.db.execute("SELECT * FROM job_classifications").fetchone()) == first
        for name, rows in before.items():
            assert [tuple(r) for r in store.db.execute(f"SELECT * FROM {name}")] == rows
        assert store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not store.db.execute("PRAGMA foreign_key_check").fetchall()
    backups = list((tmp_path / "backups").glob("*-pre-processing-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
        assert not backup.execute(
            "SELECT 1 FROM sqlite_master WHERE name='job_classifications'"
        ).fetchone()


def test_missing_and_stale_results_cannot_be_eligible():
    with Store(":memory:") as store:
        store.upsert(candidate())
        assert inspect_results(store)[0]["stale"]
        process_jobs(store)
        assert len(inspect_results(store, status="ELIGIBLE", category="Python")) == 1
        store.upsert(candidate(description="5 years experience required"))
        assert inspect_results(store, status="ELIGIBLE") == []
        result = inspect_results(store)[0]
        assert result["stale"] and result["classification"] is None
        assert process_jobs(store)["INELIGIBLE"] == 1
        assert inspect_results(store)[0]["status"] == "INELIGIBLE"
        store.db.execute("UPDATE job_classifications SET rules_version='old'")
        store.db.commit()
        assert inspect_results(store)[0]["stale"]


def test_lifecycle_and_hidden_results():
    with Store(":memory:") as store:
        store.upsert(candidate())
        process_jobs(store)
        scan = store.begin_scan("greenhouse", "acme")
        store.finish_scan(scan, Statistics(source="greenhouse", fetched=1))
        assert inspect_results(store) == []
        assert inspect_results(store, include_history=True)[0]["lifecycle"] == "closed"
        store.upsert(candidate(is_listed=False))
        process_jobs(store)
        assert inspect_results(store) == []
        store.upsert(candidate(expires_at=datetime.now(UTC) - timedelta(days=1)))
        assert inspect_results(store) == []


def test_cross_source_conflict_and_merge():
    with Store(":memory:") as store:
        store.upsert(candidate())
        store.upsert(candidate(source="ashby", description="5 years experience required"))
        process_jobs(store)
        result = inspect_results(store)[0]
        assert result["status"] == "UNVERIFIED"
        assert result["classification"]["source_conflict"]
        assert len(result["classification"]["variants"]) == 2
        store.upsert(candidate(source_id="other", source_url="https://other.test/2"))
        process_jobs(store)
        # Bridging a previously separate ID to an existing URL must not be blocked
        # by the optional table's foreign key; obsolete classifications cascade.
        store.upsert(candidate(source_id="other"))
        assert store.db.execute("SELECT count(*) FROM job_classifications").fetchone()[0] == 1
        assert inspect_results(store)[0]["stale"]


def test_cli_process_and_filter(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text('database="jobs.sqlite3"\nsources=[]', encoding="utf-8")
    with Store(tmp_path / "jobs.sqlite3") as store:
        store.upsert(candidate())
    assert main(["process", "--config", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["ELIGIBLE"] == 1
    assert (
        main(
            [
                "results",
                "--config",
                str(path),
                "--status",
                "ELIGIBLE",
                "--category",
                "Python",
                "--job-id",
                "1",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)[0]
    assert result["classification"]["variants"][0]["decisions"]["geography"]["evidence"]
    assert main(["process", "--config", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["unchanged"] == 1


@pytest.mark.parametrize(
    "source", ["himalayas", "greenhouse", "ashby", "lever", "remotive", "arbeitnow"]
)
def test_all_provider_models_are_supported(source, rows, make_collector):
    collector, _, _ = make_collector(source)
    normalized = collector.normalize(rows[source])
    with Store(":memory:") as store:
        store.upsert(normalized)
        assert process_jobs(store)["processed"] == 1
        result = inspect_results(store)[0]["classification"]["variants"][0]
        assert result["source"] == source
        assert result["workplace_type"] == normalized.workplace_type
        assert (
            json.loads(store.db.execute("SELECT payload FROM job_sources").fetchone()[0])["raw"]
            == rows[source]
        )


def test_invalid_legacy_record_is_retained_and_unverified():
    with Store(":memory:") as store:
        store.upsert(candidate())
        original = json.loads(store.db.execute("SELECT payload FROM job_sources").fetchone()[0])
        original["legacy_extension"] = True
        store.db.execute("UPDATE job_sources SET payload=?", (json.dumps(original),))
        store.db.commit()
        assert process_jobs(store)["UNVERIFIED"] == 1
        assert inspect_results(store)[0]["classification"]["variants"][0]["error"]
        assert (
            json.loads(store.db.execute("SELECT payload FROM job_sources").fetchone()[0])
            == original
        )
