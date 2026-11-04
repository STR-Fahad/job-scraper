"""Compensation geography versus genuine hiring restrictions (requirements-v3).

Every profile and assessment here is synthetic; nothing is sent to Gemini.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from test_storage import job

from job_scraper import matching
from job_scraper.candidate import CandidateProfile, ProfileEntry, profile_hash, profile_json
from job_scraper.config import MatchingConfig
from job_scraper.evidence import EvidenceError, build_segments, build_skill_catalog
from job_scraper.matching import analyze, ensure_schema, results
from job_scraper.processing import process_jobs
from job_scraper.requirements import (
    VERSION,
    ClassifiedRequirements,
    claim_text,
    discardable_location_text,
    extract_requirements,
    resolve_classifications,
    restriction_like,
)
from job_scraper.screening import classify, geography
from job_scraper.storage import Store

GITLAB = (Path(__file__).parent / "fixtures" / "job2932_description.txt").read_text(
    encoding="utf-8"
)

R028 = (
    "Clear written and spoken English communication skills, including sharing useful status "
    "updates in a remote, asynchronous environment."
)
R050 = "Country Hiring Guidelines:"
R051 = (
    "GitLab hires new team members in countries around the world. All of our roles are remote, "
    "however some roles may carry specific location-based eligibility requirements. Our Talent "
    "Acquisition team can help answer any questions about location after starting the "
    "recruiting process."
)


def items_by_id():
    return {x.id: x for x in extract_requirements(GITLAB)}


def test_gitlab_items_are_exactly_the_ones_in_the_failed_run():
    items = items_by_id()
    assert items["R028"].text == R028
    assert items["R050"].text == R050
    assert items["R051"].text == R051
    assert items["R034"].text.startswith("The base salary range for this role’s listed level")
    assert items["R034"].section == "responsibilities"
    assert GITLAB[items["R034"].start : items["R034"].end] == items["R034"].text


def test_requirements_version_is_bumped_and_invalidates_the_rule_hash(monkeypatch):
    assert VERSION == "requirements-v3"
    current = matching._rule_hash(MatchingConfig())
    monkeypatch.setattr(matching, "REQUIREMENT_VERSION", "requirements-v2")
    assert matching._rule_hash(MatchingConfig()) != current


@pytest.mark.parametrize(
    "text",
    [
        # US-resident salary range (GitLab R034, exact stored text)
        items_by_id()["R034"].text,
        "The salary range is currently for residents of the United States only, based on "
        "geographic location.",
        # country-specific compensation band mentioning a flagged word
        "Salary bands vary by country and geographic location.",
        "Pay for this role is set by the country where the employee is paid.",
        # equal-opportunity and legal boilerplate
        "We are an equal opportunity employer and do not discriminate based on country of origin.",
        "Employment decisions are made without regard to remote status, location or country.",
        # bare label
        R050,
        "Location:",
    ],
)
def test_compensation_boilerplate_and_labels_may_be_discarded(text):
    assert discardable_location_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "Candidates must reside in the United States.",
        "You must be authorized to work in the United States; authorization is required.",
        "Applicants must be a US citizen.",
        "We only hire candidates located in the United States.",
        "Visa sponsorship is not available for this country.",
        # mixed pay and hiring condition: never exempt the whole item
        "The salary range is for US residents only and you must be authorized to work in the US.",
        "The base salary is for residents of the United States. Applicants must reside in the "
        "country of hire.",
        "Pay is set by location; we only hire candidates located in Germany.",
        # worldwide and ambiguous wording is not pay information
        "This role is remote worldwide.",
        R051,
        R028,
    ],
)
def test_hiring_conditions_and_ambiguous_location_text_cannot_be_discarded(text):
    assert not discardable_location_text(text)


def inventory(*texts):
    return extract_requirements("\n".join(texts))


def classify_items(items, **kinds):
    def default(item):
        return "req" if any(not m.negated for m in item.technologies) else "duty"

    return ClassifiedRequirements.model_validate(
        {
            "items": [
                {"requirement_id": x.id, "parts": [{"kind": kinds.get(x.id, default(x))}]}
                for x in items
            ]
        }
    )


def resolve(items, response):
    profile = CandidateProfile(skills=["Ruby"], experience_years=1.25)
    _, segments = build_segments({"description": ""}, profile.model_dump())
    return resolve_classifications(
        response, items, segments, None, build_skill_catalog(segments), experience_months=15
    )


def failure(items, response):
    with pytest.raises(EvidenceError) as caught:
        resolve(items, response)
    return caught.value


def test_salary_item_can_be_nonreq_and_contributes_no_finding():
    items = inventory(
        "Experience with Ruby.",
        "The base salary range is currently for residents of the United States only.",
        "Maintain services.",
    )
    findings = resolve(items, classify_items(items, R002="nonreq"))
    assert all(x.get("job_evidence_id") != "R002" for x in findings)


def test_salary_item_classified_as_eligibility_is_still_accepted():
    items = inventory(
        "Experience with Ruby.",
        "The salary range is for residents of the United States.",
        "Maintain services.",
    )
    resolve(items, classify_items(items, R002="eligibility"))


@pytest.mark.parametrize(
    "text",
    [
        "Candidates must reside in the United States.",
        "Work authorization is required; salary is paid in USD.",
        "The salary range is for US residents and applicants must be a US citizen.",
        R051,
    ],
)
def test_genuine_restriction_discarded_as_nonreq_still_fails(text):
    items = inventory("Experience with Ruby.", text, "Maintain services.")
    error = failure(items, classify_items(items, R002="nonreq"))
    assert (error.stage, error.reason, error.requirement_id) == (
        "requirement_validation",
        "restriction_discarded",
        "R002",
    )


def test_unsupported_and_incomplete_classifications_still_fail():
    items = inventory("Experience with Ruby.", "Maintain services.")
    assert failure(items, ClassifiedRequirements(items=[])).reason == "missing_requirement_id"
    unknown = ClassifiedRequirements.model_validate(
        {"items": [{"requirement_id": "R099", "parts": [{"kind": "duty"}]}]}
    )
    assert failure(items, unknown).reason == "unknown_id"
    contradictory = ClassifiedRequirements.model_validate(
        {
            "items": [
                {"requirement_id": "R001", "parts": [{"kind": "duty"}, {"kind": "nonreq"}]},
                {"requirement_id": "R002", "parts": [{"kind": "duty"}]},
            ]
        }
    )
    assert failure(items, contradictory).reason == "contradictory_assignment"


def gitlab_response(items, **overrides):
    kinds = {}
    for item in items:
        if item.heading:
            kinds[item.id] = "nonreq"
        elif item.section == "responsibilities":
            kinds[item.id] = "duty"
        else:
            kinds[item.id] = "nonreq"
        if any(not m.negated for m in item.technologies):
            kinds[item.id] = "req"
    kinds |= {"R028": "req", "R034": "nonreq", "R050": "nonreq", "R051": "eligibility"}
    kinds |= overrides
    return classify_items(items, **kinds)


def test_mocked_gitlab_classification_validates_without_discarding_hiring_conditions():
    items = extract_requirements(GITLAB)
    findings = resolve(items, gitlab_response(items))
    referenced = {x.get("job_evidence_id") for x in findings}
    assert "R034" not in referenced and "R050" not in referenced
    assert "R051" in referenced


def test_gitlab_hiring_statement_labelled_nonreq_still_fails():
    items = extract_requirements(GITLAB)
    error = failure(items, gitlab_response(items, R051="nonreq"))
    assert (error.reason, error.requirement_id) == ("restriction_discarded", "R051")
    error = failure(items, gitlab_response(items, R028="nonreq"))
    assert (error.reason, error.requirement_id) == ("restriction_discarded", "R028")


def test_long_items_produce_quotes_that_remain_phrases_of_the_source():
    items = extract_requirements(GITLAB)
    for item in items:
        quote = claim_text(item.text)
        assert len(quote) <= 200 and quote in item.text
        assert item.text == quote or item.text[len(quote)].isspace()
    assert claim_text("short text") == "short text"
    assert claim_text("x" * 250) == "x" * 200


def test_module_two_decisions_are_independent_of_requirement_classification():
    posting = job(
        title="Backend Engineer, Source Code: Repository Services (Ruby)",
        workplace_type="remote",
        location=["Remote"],
        description=GITLAB,
    )
    result = classify(posting)
    assert geography(posting).status == "UNVERIFIED"
    assert result.status != "ELIGIBLE"


def test_mocked_job_2932_assessment_saves_hits_cache_and_never_sends_the_profile(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-only")
    profile = CandidateProfile(
        experience=[ProfileEntry(text="SYNTHETIC-MARKER Built Ruby on Rails APIs")],
        skills=["Ruby", "Git"],
        experience_years=1.25,
    )
    posting = job(
        source="arbeitnow",
        board="",
        source_id="gitlab-fixture",
        company="gitlab",
        title="Backend Engineer, Source Code: Repository Services (Ruby)",
        description=GITLAB,
        workplace_type="remote",
        source_url="https://example.com/gitlab-fixture",
    )
    with Store(":memory:") as store:
        store.upsert(posting)
        process_jobs(store)
        ensure_schema(store)
        with store.db:
            store.db.execute(
                "INSERT INTO candidate_profiles(created_at,approved,profile_hash,profile) VALUES (?,?,?,?)",
                ("2026-11-04", 1, profile_hash(profile), profile_json(profile)),
            )
        items = extract_requirements(GITLAB)
        sent = []

        def respond(**kwargs):
            sent.append(kwargs["contents"])
            request = json.loads(kwargs["contents"])
            assert list(request) == ["requirements"]
            return MagicMock(
                text=gitlab_response(items).model_dump_json(),
                usage_metadata=None,
            )

        factory = MagicMock()
        method = factory.return_value.__enter__.return_value.models.generate_content
        method.side_effect = respond
        config = MatchingConfig(retries=0, max_requests_per_run=1)
        first = analyze(store, config, live=True, job_id=1, client_factory=factory)
        assert first["requests"] == 1 and first["saved"] == 1 and not first["failures"]
        assert "SYNTHETIC-MARKER" not in sent[0]
        record = results(store, config, job_id=1)[0]
        stored = {x["requirement_id"]: x for x in record["result"]["requirement_classifications"]}
        assert [p["kind"] for p in stored["R034"]["parts"]] == ["nonreq"]
        assert record["eligibility_status"] == "UNVERIFIED"
        again = analyze(store, config, live=True, job_id=1, client_factory=factory)
        assert again["cached"] == 1 and again["requests"] == 0
        assert method.call_count == 1


MANDATORY_HIRING = [
    "Candidates must reside in the United States.",
    "Applicants must be residents of Canada.",
    "Must be legally authorized to work in the United States.",
    "Applicants must be based in Berlin.",
    "This position is only available to candidates located in Germany.",
    "This role is open only to residents of the EU.",
    "This position is limited to applicants located in India.",
    "This role is restricted to candidates residing in Poland.",
    "Must be eligible to work in the UK.",
    "Position available to US residents only.",
    "Only candidates based in Germany will be considered.",
]


@pytest.mark.parametrize("text", MANDATORY_HIRING)
def test_explicit_mandatory_hiring_conditions_are_rejected_as_nonreq(text):
    assert restriction_like(text) and not discardable_location_text(text)
    items = inventory("Experience with Ruby.", text, "Maintain services.")
    error = failure(items, classify_items(items, R002="nonreq"))
    assert (error.reason, error.requirement_id) == ("restriction_discarded", "R002")
    # The same sentence is accepted when classified as an eligibility condition.
    resolve(items, classify_items(items, R002="eligibility"))
    assert items[1].text == text


@pytest.mark.parametrize(
    "text",
    [
        "The published salary range applies to US residents.",
        "The base salary range is for residents of the United States only.",
        "Compensation varies by country of residence.",
        "We do not discriminate on the basis of race, national origin, citizenship or marital status.",
        "We are an equal opportunity employer and welcome candidates of all nationalities.",
    ],
)
def test_salary_only_and_equal_opportunity_statements_remain_discardable(text):
    assert discardable_location_text(text)
    items = inventory("Experience with Ruby.", text, "Maintain services.")
    resolve(items, classify_items(items, R002="nonreq"))


@pytest.mark.parametrize(
    "text",
    [
        "The published salary range applies to US residents. Candidates must reside in the US.",
        "Candidates must reside in the United States; the published salary range applies to US "
        "residents.",
        "The salary is for US residents and this position is only available to candidates "
        "located in the US.",
    ],
)
def test_mixed_salary_and_mandatory_hiring_text_cannot_be_discarded(text):
    assert not discardable_location_text(text)
    items = inventory("Experience with Ruby.", text, "Maintain services.")
    assert failure(items, classify_items(items, R002="nonreq")).reason == "restriction_discarded"


def test_ambiguous_location_statement_stays_non_discardable_but_may_be_unresolved():
    items = inventory("Experience with Ruby.", R051, "Maintain services.")
    assert failure(items, classify_items(items, R002="nonreq")).reason == "restriction_discarded"
    resolve(items, classify_items(items, R002="unresolved"))
    assert (
        geography(job(title="Backend Engineer", workplace_type="remote", description=R051)).status
        != "ELIGIBLE"
    )


def test_gitlab_2932_with_a_hiring_sentence_added_still_validates_only_when_not_discarded():
    mandatory = "Candidates must reside in the United States."
    description = GITLAB + "\n" + mandatory
    items = extract_requirements(description)
    last = items[-1]
    assert (last.text, description[last.start : last.end]) == (mandatory, mandatory)
    assert last.id == f"R{len(items):03d}"
    assert failure(items, gitlab_response(items, **{last.id: "nonreq"})).requirement_id == last.id
    resolve(items, gitlab_response(items, **{last.id: "eligibility"}))
