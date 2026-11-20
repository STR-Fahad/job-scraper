"""Blocker overrides: proposals change nothing; only an approval for the CURRENT assessment lifts."""

import sqlite3
from contextlib import closing

import pytest
from test_decision import experience, row
from test_storage import job

from job_scraper import overrides
from job_scraper.decision import decide
from job_scraper.storage import Store


def entry(blocker, status="approved", assessment_id=7, number=1):
    return {
        "id": number,
        "job_id": 1,
        "blocker": blocker,
        "status": status,
        "quote": "a verbatim line",
        "reason": "the line is a promise, not a requirement",
        "proposed_by": "judge",
        "assessment_id": assessment_id,
        "effective": status if assessment_id == 7 else "outdated",
    }


def gap_row(overrides_=()):
    data = row(score=80, ceiling=95, findings=[experience(60)])
    data["overrides"] = list(overrides_)
    return data


def judged_row(overrides_=()):
    eligibility = {"kind": "eligibility", "job_evidence_id": "J1", "match_state": "UNMATCHED"}
    data = row(score=80, ceiling=95, coverage=80, findings=[eligibility])
    data["judgment"] = {
        "id": 3,
        "suggested": "INELIGIBLE",
        "summary": "needs a UK base",
        "reason_codes": ["location_residency_condition"],
        "blocking_requirement_ids": ["J1"],
    }
    data["judgment_requirements"] = []
    data["overrides"] = list(overrides_)
    return data


def test_without_an_override_nothing_changes():
    result = decide(gap_row())
    assert (result["status"], result["reason_code"]) == ("INELIGIBLE", "experience_gap")
    assert "overrides_applied" not in result


@pytest.mark.parametrize("status", ["proposed", "rejected"])
def test_a_proposal_or_rejection_never_lifts_a_blocker(status):
    result = decide(gap_row([entry("experience_gap", status)]))
    assert (result["status"], result["reason_code"]) == ("INELIGIBLE", "experience_gap")


def test_an_approval_for_an_older_assessment_is_outdated_and_ignored():
    stale = entry("experience_gap", "approved", assessment_id=6)
    assert stale["effective"] == "outdated"
    result = decide(gap_row([stale]))
    assert result["reason_code"] == "experience_gap"


def test_approved_experience_gap_override_lifts_only_that_blocker():
    result = decide(gap_row([entry("experience_gap")]))
    assert result["reason_code"] != "experience_gap"
    # the score rules decide again; an override never sets ELIGIBLE directly
    assert result["reason_code"] == "suitable_fit" and result["status"] == "ELIGIBLE"
    assert result["overrides_applied"][0]["blocker"] == "experience_gap"
    assert "Override approved by you" in result["detail"]
    assert result["pakistan_hiring"] == "UNVERIFIED"


def test_approved_judgment_blocker_override_returns_the_judgment_to_the_remaining_rules():
    assert decide(judged_row())["reason_code"] == "session_judgment_ineligible"
    result = decide(judged_row([entry("judgment_blocker")]))
    assert result["reason_code"] != "session_judgment_ineligible"
    assert result["overrides_applied"][0]["blocker"] == "judgment_blocker"


def test_an_experience_override_does_not_lift_a_hiring_condition():
    # the judgment's blocker is an eligibility item; only the experience blocker was approved
    result = decide(judged_row([entry("experience_gap")]))
    assert result["reason_code"] == "session_judgment_ineligible"


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "jobs.sqlite3") as opened:
        opened.upsert(
            job(
                description="Join us. A 10 year commitment to support. Remote.",
                location=["Remote in Europe"],
            )
        )
        yield opened


def test_propose_requires_a_verbatim_quote_and_valid_fields(store):
    ok = overrides.propose(
        store, 1, "experience_gap", "A 10 year commitment", "employer promise", "judge", 7
    )
    assert ok["status"] == "proposed" and ok["assessment_id"] == 7
    # whitespace differences are tolerated, wording differences are not
    overrides.propose(store, 1, "judgment_blocker", "Remote  in\nEurope", "why", "user", 7)
    with pytest.raises(ValueError, match="verbatim"):
        overrides.propose(store, 1, "experience_gap", "a ten year promise", "why", "judge", 7)
    with pytest.raises(ValueError, match="blocker must be"):
        overrides.propose(store, 1, "manual_restriction", "Join us.", "why", "judge", 7)
    with pytest.raises(ValueError, match="reason is required"):
        overrides.propose(store, 1, "experience_gap", "Join us.", "  ", "judge", 7)
    with pytest.raises(ValueError, match="current assessment"):
        overrides.propose(store, 1, "experience_gap", "Join us.", "why", "judge", None)
    with pytest.raises(ValueError, match="No job with id"):
        overrides.propose(store, 99, "experience_gap", "Join us.", "why", "judge", 7)


def test_decisions_are_appended_and_only_the_newest_proposal_can_be_decided(store):
    first = overrides.propose(store, 1, "experience_gap", "Join us.", "why", "judge", 7)
    approved = overrides.decide(store, first["id"], "approved", "checked the posting")
    assert approved["status"] == "approved" and approved["parent_id"] == first["id"]
    assert [e["status"] for e in overrides.history(store, 1)] == ["proposed", "approved"]
    with pytest.raises(ValueError, match="newest, undecided"):
        overrides.decide(store, first["id"], "rejected")
    with pytest.raises(ValueError, match="newest, undecided"):
        overrides.decide(store, approved["id"], "rejected")
    with pytest.raises(ValueError, match="approved or rejected"):
        overrides.decide(store, first["id"], "proposed")
    with pytest.raises(ValueError, match="No override with id"):
        overrides.decide(store, 999, "approved")


def test_latest_and_effective_state_follow_the_current_assessment(store):
    first = overrides.propose(store, 1, "experience_gap", "Join us.", "why", "judge", 7)
    overrides.decide(store, first["id"], "approved")
    newest = overrides.latest(store)[1]
    assert newest[0]["status"] == "approved"
    assert overrides.annotate(newest, 7)[0]["effective"] == "approved"
    assert overrides.annotate(newest, 8)[0]["effective"] == "outdated"
    assert overrides.approved_blockers({"overrides": overrides.annotate(newest, 7)}) == {
        "experience_gap": overrides.annotate(newest, 7)[0]
    }
    assert overrides.approved_blockers({"overrides": overrides.annotate(newest, 8)}) == {}
    assert overrides.history(store) and overrides.render(overrides.history(store))


def test_the_table_is_append_only(store):
    overrides.propose(store, 1, "experience_gap", "Join us.", "why", "judge", 7)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store.db.execute("UPDATE blocker_overrides SET reason='x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store.db.execute("DELETE FROM blocker_overrides")


def test_an_existing_database_is_backed_up_before_the_table_is_created(store, tmp_path):
    assert not overrides.has_schema(store)
    assert overrides.history(store) == [] and overrides.latest(store) == {}
    overrides.ensure_schema(store)
    backups = list((tmp_path / "backups").glob("jobs-pre-overrides-*.sqlite3"))
    assert len(backups) == 1
    assert overrides.has_schema(store)
    overrides.ensure_schema(store)  # idempotent: no second backup
    assert len(list((tmp_path / "backups").glob("jobs-pre-overrides-*.sqlite3"))) == 1
    with closing(sqlite3.connect(backups[0])) as saved:
        assert saved.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_cli_override_commands(tmp_path, capsys):
    from job_scraper import cli

    config = tmp_path / "config.toml"
    config.write_text('[[sources]]\nsource="arbeitnow"', encoding="utf-8")
    with Store(tmp_path / "data/jobs.sqlite3") as opened:
        opened.upsert(job(description="Join us. A 10 year commitment to support."))
    base = ["override", "--config", str(config)]
    assert cli.main([*base, "list"]) == 0
    assert "No blocker overrides recorded." in capsys.readouterr().out
    # nothing to override: the job has no current assessment, so it is not decided by a blocker
    propose = [
        *base,
        "propose",
        "--job-id",
        "1",
        "--blocker",
        "experience_gap",
        "--quote",
        "Join us.",
        "--reason",
        "why",
    ]
    assert cli.main(propose) == 2
    assert "nothing to override" in capsys.readouterr().err
    assert cli.main([*base, "propose", "--job-id", "1"]) == 2
    assert cli.main([*base, "approve"]) == 2
    assert cli.main([*base, "approve", "--override-id", "5"]) == 2
