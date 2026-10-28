import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from job_scraper.candidate import CandidateProfile, ProfileEntry, profile_hash, profile_json
from job_scraper.config import MatchingConfig
from job_scraper.evidence import EvidenceError, build_segments
from job_scraper.matching import analyze, ensure_schema, results
from job_scraper.models import Job
from job_scraper.processing import process_jobs
from job_scraper.requirements import (
    ClassifiedRequirements,
    extract_requirements,
    extract_technologies,
    resolve_classifications,
)
from job_scraper.storage import Store

JOB22 = Path(__file__).parent / "fixtures" / "job22_description.txt"


def _classifications(items):
    output = []
    for item in items:
        parts = [{"kind": "nonreq"}]
        if item.heading:
            pass
        elif item.id == "R003":
            parts = [{"kind": "eligibility"}]
        elif item.id == "R013":
            parts = [{"kind": "exp"}]
        elif item.id in {"R015", "R016"}:
            parts = [{"kind": "req"}]
        elif item.section == "requirements":
            parts = [{"kind": "req"}]
        elif item.section == "preferred":
            parts = [{"kind": "pref"}]
        elif item.id in {"R007", "R008", "R009"}:
            parts = [{"kind": "duty"}]
        output.append({"requirement_id": item.id, "parts": parts})
    return output


def test_job22_inventory_preserves_sections_locations_and_all_nine_requirements():
    description = JOB22.read_text(encoding="utf-8")
    items = extract_requirements(description)
    assert len(items) == 30
    assert [x.id for x in items] == [f"R{i:03d}" for i in range(1, 31)]
    assert len([x for x in items if x.section == "requirements" and not x.heading]) == 9
    assert len([x for x in items if x.section == "preferred" and not x.heading]) == 2
    assert all(description[x.start : x.end] == x.text for x in items)
    assert items[12].text == "2+ years of professional software engineering"
    assert {x.text for x in items[14].technologies} == {
        "Python",
        "TypeScript",
        "Go",
        "Rust",
        "Java",
        "C++",
    }
    assert {x.text for x in items[15].technologies} == {
        "Docker",
        "Linux",
        "Git",
        "automated testing",
    }


def test_technology_inventory_aliases_negation_and_no_inferred_neighbors():
    mentions = extract_technologies(
        "Ruby on Rails and Rails; REST APIs and JavaScript. No Python required. "
        "Python not required. Go and Rust."
    )
    assert [x.canonical for x in mentions[:2]] == ["ruby on rails", "ruby on rails"]
    assert all(x.negated for x in mentions if x.canonical == "python")
    assert any(x.text == "JavaScript" and not x.negated for x in mentions)
    assert not any(x.canonical == "react" for x in mentions)
    assert not any(x.canonical == "python" and not x.negated for x in mentions)


def test_generated_skill_list_is_rejected_by_response_schema():
    with pytest.raises(ValidationError):
        ClassifiedRequirements.model_validate(
            {
                "items": [
                    {"requirement_id": "R001", "parts": [{"kind": "req", "skills": ["Python"]}]}
                ]
            }
        )


def test_unknown_and_negated_technology_remain_unverified():
    description = "Requirements\nMust know Zig\nNo Python required\nBuild systems"
    inventory = extract_requirements(description)
    _, profile = build_segments(
        {}, CandidateProfile(skills=["Python"], experience_years=1.25).model_dump()
    )
    assert not inventory[1].technologies
    assert inventory[2].technologies[0].negated
    response = ClassifiedRequirements.model_validate(
        {
            "items": [
                {"requirement_id": "R001", "parts": [{"kind": "nonreq"}]},
                {"requirement_id": "R002", "parts": [{"kind": "req"}]},
                {"requirement_id": "R003", "parts": [{"kind": "req"}]},
                {"requirement_id": "R004", "parts": [{"kind": "duty"}]},
            ]
        }
    )
    result = resolve_classifications(response, inventory, profile, 1.25)
    assert [x["match_state"] for x in result] == ["UNVERIFIED", "UNVERIFIED", "UNVERIFIED"]


def test_mixed_requirement_line_derives_skill_and_years_locally():
    inventory = extract_requirements("Required Rails and 2+ years. Build Rails APIs.")
    profile_data = CandidateProfile(
        experience=[ProfileEntry(text="Built Ruby on Rails APIs professionally")],
        experience_years=1.25,
    ).model_dump()
    _, profile = build_segments({}, profile_data)
    response = ClassifiedRequirements.model_validate(
        {
            "items": [
                {
                    "requirement_id": "R001",
                    "parts": [{"kind": "req"}, {"kind": "exp"}, {"kind": "duty"}],
                }
            ]
        }
    )
    result = resolve_classifications(response, inventory, profile, 1.25)
    assert [x["match_state"] for x in result] == ["MATCHED", "UNMATCHED", "MATCHED"]


def test_wrapped_markdown_bullets_and_numbered_items_preserve_original_source():
    description = (
        "## Requirements ##\n- Build Python and\n  Docker services\n"
        "2. Have 2+ years\n   of engineering experience\n\n"
        "### Responsibilities\n* Maintain APIs\n"
    )
    items = extract_requirements(description)
    assert [x.section for x in items] == ["requirements"] * 3 + ["responsibilities"] * 2
    assert items[1].text == "- Build Python and\n  Docker services"
    assert items[1].start_line == 2 and items[1].end_line == 3
    assert items[2].text == "2. Have 2+ years\n   of engineering experience"
    assert all(description[x.start : x.end] == x.text for x in items)


def test_missing_invented_duplicate_and_misleading_ids_rejected():
    items = extract_requirements(JOB22.read_text(encoding="utf-8"))
    _, profile = build_segments({}, CandidateProfile(experience_years=1.25).model_dump())
    good = _classifications(items)
    variants = [
        (good[:-1], "missing_requirement_id"),
        (
            [*good, {"requirement_id": "R999", "parts": [{"kind": "nonreq"}]}],
            "unknown_id",
        ),
        ([*good, good[0]], "duplicate_id"),
    ]
    for assignments, reason in variants:
        with pytest.raises(EvidenceError, match=reason):
            resolve_classifications(ClassifiedRequirements(items=assignments), items, profile, 1.25)
    wrong = [dict(x) for x in good]
    wrong[12] = {"requirement_id": "R013", "parts": [{"kind": "req"}]}
    with pytest.raises(EvidenceError, match="experience_minimum_misclassified") as error:
        resolve_classifications(ClassifiedRequirements(items=wrong), items, profile, 1.25)
    assert error.value.requirement_id == "R013"
    assert error.value.classification == "req"
    assert "2+ years" in error.value.source_context
    contradictory = [dict(x) for x in good]
    contradictory[15] = {
        "requirement_id": "R016",
        "parts": [
            {"kind": "req"},
            {"kind": "nonreq"},
        ],
    }
    with pytest.raises(EvidenceError, match="contradictory_assignment"):
        resolve_classifications(ClassifiedRequirements(items=contradictory), items, profile, 1.25)
    hidden_clear_skill = [dict(x) for x in good]
    hidden_clear_skill[15] = {
        "requirement_id": "R016",
        "parts": [{"kind": "unresolved"}],
    }
    with pytest.raises(EvidenceError, match="clear_skill_marked_unresolved"):
        resolve_classifications(
            ClassifiedRequirements(items=hidden_clear_skill), items, profile, 1.25
        )


def test_job22_mocked_complete_assessment_saves_and_hits_cache(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-only")
    description = JOB22.read_text(encoding="utf-8")
    profile = CandidateProfile(
        experience=[ProfileEntry(text="Built Python model training pipeline in a project")],
        skills=["Docker", "Git"],
        experience_years=1.25,
    )
    job = Job(
        source="remotive",
        source_id="job22-fixture",
        company="Askable",
        title="Software Engineer, AI Coding Tasks",
        description=description,
        workplace_type="remote",
        source_url="https://example.com/job22-fixture",
        attribution="Synthetic",
        attribution_url="https://example.com",
    )
    with Store(":memory:") as store:
        store.upsert(job)
        process_jobs(store)
        ensure_schema(store)
        with store.db:
            store.db.execute(
                "INSERT INTO candidate_profiles(created_at,approved,profile_hash,profile) VALUES (?,?,?,?)",
                ("2026-10-29", 1, profile_hash(profile), profile_json(profile)),
            )

        def respond(**kwargs):
            request = json.loads(kwargs["contents"])
            assert list(request) == ["requirements"]
            assert len(request["requirements"]) == 30
            return MagicMock(
                text=json.dumps({"items": _classifications(extract_requirements(description))}),
                usage_metadata=None,
            )

        factory = MagicMock()
        method = factory.return_value.__enter__.return_value.models.generate_content
        method.side_effect = respond
        config = MatchingConfig(retries=0, max_requests_per_run=1)
        first = analyze(store, config, live=True, job_id=1, client_factory=factory)
        assert first["requests"] == 1 and first["saved"] == 1
        record = results(store, config, job_id=1)[0]
        assert record["score"] == 0
        assert record["result"]["breakdown"]["experience"]["unmatched"] == 1
        assert len(record["result"]["requirement_classifications"]) == 30
        again = analyze(store, config, live=True, job_id=1, client_factory=factory)
        assert again["cached"] == 1 and again["requests"] == 0
        assert method.call_count == 1
