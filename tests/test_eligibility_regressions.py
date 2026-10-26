"""Permanent reproduction of the Module 2 audit cases, independent of work/ files."""

import json
import sqlite3
from pathlib import Path

import pytest
from test_processing import candidate

from job_scraper.countries import country_code
from job_scraper.models import Job
from job_scraper.processing import VERSION, ensure_schema, process_jobs
from job_scraper.screening import classify
from job_scraper.storage import Store

CASES = json.loads(
    (Path(__file__).parent / "fixtures/module2_adversarial.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_audit_regression(case):
    result = classify(Job(**case["input"]))
    assert result.status == case["expected"], result.model_dump_json(indent=2)


@pytest.mark.parametrize(
    "description,restrictions,expected",
    [
        ("Work from Pakistan. Must be based in the US.", {}, "UNVERIFIED"),
        ("Work from Pakistan. We cannot employ anyone working from Pakistan.", {}, "UNVERIFIED"),
        ("Must be based in the US. Overlap with US business hours required.", {}, "INELIGIBLE"),
        ("", {"locationRestrictions": ["PT"]}, "INELIGIBLE"),
        ("", {"locationRestrictions": ["Portugal"]}, "INELIGIBLE"),
        ("", {"locationRestrictions": ["PK", "PT"]}, "ELIGIBLE"),
        ("", {"locationRestrictions": [{"name": "Pakistan", "alpha2": "US"}]}, "UNVERIFIED"),
        ("", {"locationRestrictions": ["ZZ"]}, "UNVERIFIED"),
        ("Work from anywhere within Europe.", {}, "INELIGIBLE"),
        ("Work from anywhere in EMEA.", {}, "UNVERIFIED"),
        ("Remote", {}, "UNVERIFIED"),
        ("Homeoffice ist nicht vorgesehen.", {}, "UNVERIFIED"),
        ("Nur Bewerber aus Deutschland.", {}, "UNVERIFIED"),
        ("Trabajo remoto desde España.", {}, "UNVERIFIED"),
    ],
)
def test_geography_constraints(description, restrictions, expected):
    result = classify(candidate(description=description, restrictions=restrictions))
    assert result.decisions["geography"].status == expected


@pytest.mark.parametrize(
    "extra,expected",
    [
        ("Must be a US citizen.", "UNVERIFIED"),
        ("Must be authorized to work in the US.", "UNVERIFIED"),
        ("No visa sponsorship available.", "UNVERIFIED"),
        ("We cannot employ anyone working from Pakistan.", "INELIGIBLE"),
        ("Security clearance required.", "UNVERIFIED"),
    ],
)
def test_independent_employment_restrictions(extra, expected):
    result = classify(
        candidate(
            description="2 years experience. Work from Pakistan. No employment restrictions. "
            + extra
        )
    )
    assert result.decisions["restrictions"].status == expected


def test_explicit_evidence_resolves_legal_dimension_without_magic_phrase():
    result = classify(
        candidate(
            description=(
                "2 years experience. Open to applicants based in Pakistan. "
                "We can legally hire people in Pakistan regardless of citizenship. "
                "No foreign work authorization is required."
            )
        )
    )
    assert result.status == "ELIGIBLE", result.model_dump_json()
    for name in ("category", "experience", "geography", "restrictions", "workplace"):
        assert result.decisions[name].status == "ELIGIBLE"
        assert result.decisions[name].evidence


def test_geographic_invitation_does_not_resolve_authorization():
    result = classify(
        candidate(description="2 years experience. Open to applicants based in Pakistan.")
    )
    assert result.decisions["geography"].status == "ELIGIBLE"
    assert result.decisions["restrictions"].status == "UNVERIFIED"
    assert result.status == "UNVERIFIED"


def test_equivalent_experience_in_lieu_of_degree_does_not_reduce_required_years():
    result = classify(
        candidate(
            description="5 years industry experience. A Bachelor's degree or equivalent work experience."
        )
    )
    assert result.decisions["experience"].status == "INELIGIBLE"


def test_role_specific_technology_fundamentals_are_not_incidental():
    result = classify(
        candidate(
            title="Software Engineer", description="Strong Python or TypeScript fundamentals."
        )
    )
    assert result.decisions["category"].status == "ELIGIBLE"
    assert "Python" in result.categories


def test_contradictory_employment_permission_needs_review():
    result = classify(
        candidate(
            description=(
                "2 years experience. Work from Pakistan. "
                "We can legally employ anyone working from Pakistan regardless of citizenship. "
                "We cannot employ anyone working from Pakistan. "
                "No foreign work authorization is required."
            )
        )
    )
    assert result.decisions["restrictions"].status == "UNVERIFIED"
    assert result.status == "UNVERIFIED"


def test_unparsed_location_text_cannot_dilute_explicit_foreign_only_field():
    result = classify(
        candidate(
            description="Remote worldwide except certain countries.",
            restrictions={"candidate_required_location": "United States"},
        )
    )
    assert result.decisions["geography"].status == "INELIGIBLE"


@pytest.mark.parametrize(
    "description,expected",
    [
        ("Ruby (5+ years, expert-level)", "INELIGIBLE"),
        ("2+ years of professional software engineering", "ELIGIBLE"),
        ("5 years of Python or Java experience", "INELIGIBLE"),
        ("5 years experience or 2 years experience", "UNVERIFIED"),
        ("5 years experience, with 2 years management or leadership experience", "INELIGIBLE"),
        ("2 years experience required. Bonus qualifications:\n5 years experience", "ELIGIBLE"),
        ("No more than 5 years experience", "ELIGIBLE"),
        ("2-3 years experience", "ELIGIBLE"),
        ("Our company has 20 years experience", "UNVERIFIED"),
        (
            "5 years experience. A Master's degree may count as two years of experience.",
            "UNVERIFIED",
        ),
    ],
)
def test_experience_modifiers(description, expected):
    assert classify(candidate(description=description)).decisions["experience"].status == expected


@pytest.mark.parametrize(
    "title,description,expected",
    [
        ("Software Engineer", "You will build backend services in Python.", "ELIGIBLE"),
        ("Mechanical Engineer", "Python experience preferred", "UNVERIFIED"),
        ("Sales Engineer", "We sell Python tools", "UNVERIFIED"),
        ("Software Engineer", "Python is not used", "UNVERIFIED"),
        ("Software Engineer", "Our company develops Python tools", "UNVERIFIED"),
    ],
)
def test_role_specific_relevance(title, description, expected):
    result = classify(candidate(title=title, description=description))
    assert result.decisions["category"].status == expected


def test_history_upgrade_preserves_existing_result_and_merge(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    with Store(path) as store:
        store.upsert(candidate())
        # Exact pre-history schema and row: this simulates the existing v2 module.
        store.db.execute("""CREATE TABLE job_classifications (
            job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
            rules_version TEXT NOT NULL, fingerprint TEXT NOT NULL,
            processed_at TEXT NOT NULL, result TEXT NOT NULL)""")
        old = (1, "pakistan-0-3-v2", "legacy", "2026-01-01T00:00:00Z", '{"status":"UNVERIFIED"}')
        store.db.execute("INSERT INTO job_classifications VALUES(?,?,?,?,?)", old)
        store.db.commit()
        ensure_schema(store)
        assert (
            tuple(
                store.db.execute(
                    "SELECT job_id,rules_version,fingerprint,processed_at,result FROM job_classification_history"
                ).fetchone()
            )
            == old
        )
        assert tuple(store.db.execute("SELECT * FROM job_classifications").fetchone()) == old
        process_jobs(store)
        assert (
            store.db.execute("SELECT count(*) FROM job_classification_history").fetchone()[0] == 2
        )
        assert process_jobs(store)["unchanged"] == 1
        assert (
            store.db.execute("SELECT count(*) FROM job_classification_history").fetchone()[0] == 2
        )
        assert (
            store.db.execute("SELECT rules_version FROM job_classifications").fetchone()[0]
            == VERSION
        )
        store.upsert(candidate(source_id="second", source_url="https://example.test/2"))
        process_jobs(store)
        count = store.db.execute("SELECT count(*) FROM job_classification_history").fetchone()[0]
        store.upsert(candidate(source_id="second"))
        assert (
            store.db.execute("SELECT count(*) FROM job_classification_history").fetchone()[0]
            == count
        )
        assert store.db.execute("SELECT count(*) FROM job_classifications").fetchone()[0] == 1
        assert not store.db.execute("PRAGMA foreign_key_check").fetchall()
    backups = list((tmp_path / "backups").glob("*-pre-processing-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert tuple(db.execute("SELECT * FROM job_classifications").fetchone()) == old


@pytest.mark.parametrize(
    "label,code",
    [
        ("PK", "PK"),
        ("PAK", "PK"),
        ("Pakistan", "PK"),
        ("Portugal", "PT"),
        ("U.S.", "US"),
        ("ZZ", None),
        ("840", None),
        ("Narnia", None),
    ],
)
def test_validated_country_catalog(label, code):
    assert country_code(label) == code


def test_history_migration_rolls_back_on_ddl_failure():
    with Store(":memory:") as store:
        store.upsert(candidate())
        process_jobs(store)
        old = tuple(store.db.execute("SELECT * FROM job_classifications").fetchone())
        store.db.execute("DROP TRIGGER classification_history_insert")
        store.db.execute("DROP TRIGGER classification_history_update")
        store.db.execute("DROP TABLE job_classification_history")
        store.db.commit()
        store.db.set_authorizer(
            lambda action, *args: (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_CREATE_TRIGGER
                else sqlite3.SQLITE_OK
            )
        )
        with pytest.raises(sqlite3.DatabaseError):
            ensure_schema(store)
        store.db.set_authorizer(None)
        assert not store.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='job_classification_history'"
        ).fetchone()
        assert tuple(store.db.execute("SELECT * FROM job_classifications").fetchone()) == old
