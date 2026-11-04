"""Module 2 targeted parsing corrections (pakistan-0-3-v3.5)."""

import pytest
from test_storage import job

from job_scraper.screening import classify, experience, geography

JOB_550 = (
    "Implement the above role-based access control with best practices, enforced in "
    "Convex functions, not only in the UI."
)


def worldwide(description):
    return job(
        title="Backend Engineer",
        workplace_type="remote",
        location=["Worldwide"],
        restrictions={"locationRestrictions": ["Worldwide"]},
        description=description,
    )


@pytest.mark.parametrize(
    "compound", ["role-based", "skill-based", "project-based", "cloud-based", "Role-Based"]
)
def test_non_location_based_compound_is_not_a_geography_exception(compound):
    result = geography(worldwide(f"Implement {compound} access control, not only in the UI."))
    assert result.status == "ELIGIBLE", result.evidence


def test_job_550_clause_keeps_worldwide_geography():
    result = geography(worldwide(JOB_550))
    assert result.status == "ELIGIBLE"
    assert all(item.rule != "explicit_geography" for item in result.evidence)


@pytest.mark.parametrize(
    "text",
    [
        "We do not hire UK-based candidates only.",
        "Candidates must not be EU-based.",
        "This role is not open to Berlin-based applicants.",
        "Remote, unless you are non-US-based.",
        "We do not hire anyone based in India.",
        "Not available to candidates located in Pakistan.",
    ],
)
def test_genuine_location_exceptions_still_need_review(text):
    assert geography(worldwide(text)).status != "ELIGIBLE"


def test_explicit_country_restriction_and_exclusion_still_work():
    assert geography(worldwide("Work from Germany.")).status == "UNVERIFIED"
    assert (
        geography(worldwide("Open to candidates based in the United States.")).status != "ELIGIBLE"
    )
    assert geography(worldwide("We do not hire from Pakistan.")).status == "UNVERIFIED"
    germany = job(
        title="Backend Engineer", workplace_type="remote", description="Work from Germany."
    )
    assert geography(germany).status == "INELIGIBLE"


def minima(description):
    exp, extracted = experience(job(description=description))
    return exp.status, [(x["minimum_years"], x["optional"]) for x in extracted]


@pytest.mark.parametrize(
    "description,status,expected",
    [
        ("At least five years of experience in security engineering.", "INELIGIBLE", [(5, False)]),
        ("Five years of hands-on experience in engineering.", "INELIGIBLE", [(5, False)]),
        (
            "Minimum of seven years’ experience in RF systems development.",
            "INELIGIBLE",
            [(7, False)],
        ),
        ("[3+] years as a Backend Engineer with Node.js and TypeScript.", "ELIGIBLE", [(3, False)]),
        ("[4+] years of software development experience.", "INELIGIBLE", [(4, False)]),
        ("(5+) years of experience in software development.", "INELIGIBLE", [(5, False)]),
        ("Five (5) years of software development experience.", "INELIGIBLE", [(5, False)]),
        ("Four to seven years of relevant experience.", "INELIGIBLE", [(4, False)]),
        ("Three to six years of software engineering experience.", "ELIGIBLE", [(3, False)]),
        (
            "At least three years of hands-on experience in software development.",
            "ELIGIBLE",
            [(3, False)],
        ),
        ("Two years or more of software development experience.", "ELIGIBLE", [(2, False)]),
        ("One year of Python experience.", "ELIGIBLE", [(1, False)]),
        ("Six months of software development experience.", "ELIGIBLE", [(0.5, False)]),
    ],
)
def test_number_words_and_brackets_are_minimums(description, status, expected):
    assert minima(description) == (status, expected)


@pytest.mark.parametrize(
    "description,status",
    [
        ("Up to five years of professional experience.", "ELIGIBLE"),
        ("Five years of software development experience preferred.", "UNVERIFIED"),
        ("Four years of Python experience or three years of Java experience.", "UNVERIFIED"),
        ("Bachelor's degree or six years of relevant working experience.", "UNVERIFIED"),
        ("Around five years of relevant experience would be helpful.", "UNVERIFIED"),
    ],
)
def test_maximum_preferred_and_alternative_wording_is_not_a_hard_minimum(description, status):
    exp, extracted = experience(job(description=description))
    assert exp.status == status
    if status == "ELIGIBLE":
        assert [x["minimum_years"] for x in extracted] == [0]


@pytest.mark.parametrize(
    "description",
    [
        "A one year contract with strong software development experience wanted.",
        "Six month probation period for software development experience reviews.",
        "A two year fixed-term engagement; software development experience helps.",
        "Notice period of three months; software development experience required.",
        "Apply within the next five years of experience? Deadline in six months for software roles.",
        "Client onboarding experience within the last three years.",
        "The two biggest engineering projects of the next two years are yours.",
        "After one year, you move into a permanent software development role.",
        "During your first six months you build software development experience.",
        "Own the full sales cycle, typically a few weeks to two months, from first touch to signed deal.",
        "Not five years of engineering with some security in it.",
    ],
)
def test_durations_that_are_not_experience_are_ignored(description):
    assert minima(description)[1] == []


def test_job_with_five_year_word_minimum_is_ineligible_overall():
    result = classify(
        job(
            title="Backend Engineer",
            workplace_type="remote",
            location=["Worldwide"],
            description="At least five years of experience building backend software.",
        )
    )
    assert result.decisions["experience"].status == "INELIGIBLE"
    assert result.status == "INELIGIBLE"


GITLAB_2932 = (
    "GitLab will not tolerate discrimination or harassment based on any of these characteristics."
)
TWILIO_2492 = (
    "We do not discriminate based upon race, religion, color, national origin, sex, "
    "sexual orientation, gender identity, age or disability."
)


@pytest.mark.parametrize(
    "text",
    [
        GITLAB_2932,
        TWILIO_2492,
        "We do not allow discrimination based on protected characteristics.",
        "Pay is not fixed and is based on experience.",
        "No commute: decisions are based on performance, not hours.",
        "We do not use performance-based or experience-based or evidence-based shortcuts.",
    ],
)
def test_unrelated_based_on_wording_is_not_a_geography_exception(text):
    result = geography(worldwide(text))
    assert result.status == "ELIGIBLE", result.evidence
    assert all(item.rule != "explicit_geography" for item in result.evidence)


def test_equal_opportunity_wording_does_not_infer_eligibility():
    no_territory = job(title="Backend Engineer", workplace_type="remote", description=GITLAB_2932)
    assert geography(no_territory).status == "UNVERIFIED"


def test_unrelated_wording_does_not_hide_a_real_restriction():
    text = f"{GITLAB_2932} Candidates must be located in Germany."
    assert geography(worldwide(text)).status != "ELIGIBLE"
    germany = job(title="Backend Engineer", workplace_type="remote", description=text)
    assert geography(germany).status == "INELIGIBLE"


@pytest.mark.parametrize(
    "text",
    [
        "We do not hire US-based candidates.",
        "Not open to non-US-based applicants.",
        "Remote, but not for UK-based candidates.",
        "No applicants based in India.",
        "We do not hire anyone based on-site in Berlin.",
    ],
)
def test_location_based_exceptions_survive_based_on_handling(text):
    assert geography(worldwide(text)).status != "ELIGIBLE"
