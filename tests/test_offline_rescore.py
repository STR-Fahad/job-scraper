"""Versioned offline rescoring of saved Gemini classifications (no API client)."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from job_scraper import matching
from job_scraper.candidate import CandidateProfile, ProfileEntry, profile_hash, profile_json
from job_scraper.config import MatchingConfig
from job_scraper.matching import analyze, ensure_schema, rescore, results
from job_scraper.models import Job
from job_scraper.processing import process_jobs
from job_scraper.requirements import extract_requirements
from job_scraper.storage import Store

JOB22 = Path(__file__).parent / "fixtures" / "job22_description.txt"
CONFIG = MatchingConfig(retries=0, max_requests_per_run=1)


def _profile(extra_skill=None):
    return CandidateProfile(
        experience=[ProfileEntry(text="Built Ruby on Rails APIs professionally")],
        projects=[
            ProfileEntry(
                text="DeepFake Detection | Python, PyTorch. Built Python model training pipeline"
            )
        ],
        skills=["Docker", "Git", "Linux", "Python"] + ([extra_skill] if extra_skill else []),
        experience_years=1.25,
    )


def _job(description=None):
    return Job(
        source="remotive",
        source_id="job22-fixture",
        company="Askable",
        title="Software Engineer, AI Coding Tasks",
        description=description or JOB22.read_text(encoding="utf-8"),
        workplace_type="remote",
        source_url="https://example.com/job22-fixture",
        attribution="Synthetic",
        attribution_url="https://example.com",
    )


def _classify(item):
    if item.heading:
        return [{"kind": "nonreq"}]
    if item.id == "R013":
        return [{"kind": "exp"}]
    if item.id in {"R003", "R020"}:
        return [{"kind": "eligibility"}]
    if item.section == "requirements":
        return [{"kind": "req"}]
    if item.section == "preferred":
        return [{"kind": "pref"}]
    if item.section == "process":
        return [{"kind": "duty"}]
    return [{"kind": "nonreq"}]


def _factory():
    def respond(**kwargs):
        description = JOB22.read_text(encoding="utf-8")
        sent = json.loads(kwargs["contents"])["requirements"]
        items = {x.id: x for x in extract_requirements(description)}
        output = [{"requirement_id": x["id"], "parts": _classify(items[x["id"]])} for x in sent]
        return MagicMock(
            text=json.dumps({"items": output}),
            usage_metadata=MagicMock(
                prompt_token_count=3000, candidates_token_count=600, total_token_count=3600
            ),
        )

    factory = MagicMock()
    factory.return_value.__enter__.return_value.models.generate_content.side_effect = respond
    return factory


def _setup(store, profile=None):
    store.upsert(_job())
    process_jobs(store)
    ensure_schema(store)
    profile = profile or _profile()
    with store.db:
        store.db.execute(
            "INSERT INTO candidate_profiles(created_at,approved,profile_hash,profile) "
            "VALUES (?,?,?,?)",
            ("2026-10-27", 1, profile_hash(profile), profile_json(profile)),
        )


def _historic(store, monkeypatch, **versions):
    """A live row saved under older rule versions, as job 22 was under score-v4."""
    defaults = {"SCORING_VERSION": "score-v4", "EVIDENCE_VERSION": "evidence-v7"}
    with monkeypatch.context() as patch:
        for name, value in {**defaults, **versions}.items():
            patch.setattr(matching, name, value)
        out = analyze(store, CONFIG, live=True, client_factory=_factory())
    assert out["saved"] == 1
    return store.db.execute("SELECT * FROM ai_assessments ORDER BY id DESC").fetchone()


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)


def test_rescore_matches_live_production_path_without_api(monkeypatch):
    with Store(":memory:") as store:
        _setup(store)
        original = tuple(_historic(store, monkeypatch))
        before = {
            name: store.db.execute(f"SELECT * FROM {name} ORDER BY rowid").fetchall()
            for name in ("jobs", "job_sources", "job_classifications", "candidate_profiles")
        }
        # Any attempt to construct an API client fails the test.
        monkeypatch.setattr(matching.genai, "Client", MagicMock(side_effect=AssertionError))
        out = rescore(store, CONFIG)
        assert (out["saved"], out["requests"], out["cached"]) == (1, 0, 0)
        rows = store.db.execute("SELECT * FROM ai_assessments ORDER BY id").fetchall()
        assert len(rows) == 2 and tuple(rows[0]) == original
        new = json.loads(rows[1]["result"])
        assert rows[1]["rule_hash"] == matching._rule_hash(CONFIG) != original[7]
        assert new["scoring_version"] == "score-v5" and new["assessment_mode"] == "offline_rescore"
        assert new["provenance"]["source_assessment_id"] == original[0]
        assert new["provenance"]["source_scoring_version"] == "score-v4"
        assert json.loads(rows[1]["token_usage"]) == {}
        assert (
            new["requirement_classifications"]
            == json.loads(original[11])["requirement_classifications"]
        )
        assert all(
            store.db.execute(f"SELECT * FROM {name} ORDER BY rowid").fetchall() == data
            for name, data in before.items()
        )
    # A fresh live score-v5 assessment with the same classifications is identical.
    monkeypatch.undo()
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    with Store(":memory:") as other:
        _setup(other)
        analyze(other, CONFIG, live=True, client_factory=_factory())
        live = json.loads(other.db.execute("SELECT result FROM ai_assessments").fetchone()[0])
    for key in ("assessment_mode", "provenance"):
        new.pop(key, None)
        live.pop(key, None)
    assert new == live


def test_rescore_preserves_history_staleness_and_cache(monkeypatch):
    with Store(":memory:") as store:
        _setup(store)
        _historic(store, monkeypatch)
        assert [x["stale"] for x in results(store, CONFIG)] == [True]
        assert results(store, CONFIG)[0]["assessment_mode"] == "live"
        rescore(store, CONFIG)
        listed = results(store, CONFIG)
        assert [(x["stale"], x["assessment_mode"]) for x in listed] == [
            (False, "offline_rescore"),
            (True, "live"),
        ]
        assert listed[0]["eligibility_status"] == listed[1]["eligibility_status"] == "UNVERIFIED"
        assert listed[0]["result"]["breakdown"]["experience"]["unmatched"] == 1
        again = rescore(store, CONFIG)
        assert (again["saved"], again["cached"]) == (0, 1)
        factory = _factory()
        cached = analyze(store, CONFIG, live=True, client_factory=factory)
        assert (cached["cached"], cached["requests"]) == (1, 0)
        factory.assert_not_called()
        assert store.db.execute("SELECT count(*) FROM ai_assessments").fetchone()[0] == 2


@pytest.mark.parametrize(
    "change",
    ["job_content", "prompt_version", "requirement_version", "weights", "profile", "none"],
)
def test_rescore_refuses_incompatible_or_missing_sources(monkeypatch, change):
    with Store(":memory:") as store:
        _setup(store)
        config = CONFIG
        if change == "prompt_version":
            _historic(store, monkeypatch, PROMPT_VERSION="match-v8")
        elif change == "requirement_version":
            _historic(store, monkeypatch, REQUIREMENT_VERSION="requirements-v1")
        elif change != "none":
            _historic(store, monkeypatch)
        if change == "job_content":
            store.upsert(_job(JOB22.read_text(encoding="utf-8") + "\nNew line"))
        elif change == "weights":
            config = MatchingConfig(
                retries=0,
                max_requests_per_run=1,
                weights={
                    "required_skills": 50,
                    "experience": 20,
                    "role_alignment": 20,
                    "preferred_projects": 10,
                },
            )
        elif change == "profile":
            revised = _profile("SQL")
            with store.db:
                store.db.execute(
                    "INSERT INTO candidate_profiles(created_at,approved,profile_hash,profile) "
                    "VALUES (?,?,?,?)",
                    ("2026-11-02", 1, profile_hash(revised), profile_json(revised)),
                )
        count = store.db.execute("SELECT count(*) FROM ai_assessments").fetchone()[0]
        out = rescore(store, config)
        assert out["saved"] == 0 and out["requests"] == 0
        assert out["skipped"] == [{"job_id": 1, "reason": "no_compatible_saved_classifications"}]
        assert store.db.execute("SELECT count(*) FROM ai_assessments").fetchone()[0] == count


def test_rescore_requires_current_approved_profile(monkeypatch):
    with Store(":memory:") as store:
        _setup(store)
        _historic(store, monkeypatch)
        draft = _profile("SQL")
        with store.db:
            store.db.execute(
                "INSERT INTO candidate_profiles(created_at,approved,profile_hash,profile) "
                "VALUES (?,?,?,?)",
                ("2026-11-02", 0, profile_hash(draft), profile_json(draft)),
            )
        with pytest.raises(ValueError, match="newer draft"):
            rescore(store, CONFIG)
