"""Module 2 defects found by auditing the November 2026 refresh (pakistan-0-3-v3.7)."""

import pytest
from test_storage import job

from job_scraper.screening import employment, experience, geography, workplace


def years(description):
    exp, extracted = experience(job(title="Software Engineer", description=description))
    return exp.status, [(x["minimum_years"], x["optional"]) for x in extracted]


@pytest.mark.parametrize(
    "description,status,minimum",
    [
        ("What you'll bring\nExperience:\n6+ years\nStrong teamwork", "INELIGIBLE", 6),
        ("Experience & Skills:\n5+ years\nPython", "INELIGIBLE", 5),
        ("Experience Required:\n2-3+ years of hands-on\nGit", "ELIGIBLE", 2),
        ("✅  5-7+ years of a strong frontend background.", "INELIGIBLE", 5),
        ("You have been coding full time professionally for 4 to 5 plus years", "INELIGIBLE", 4),
        (
            "12+ yrs of relevant industry experience",
            "INELIGIBLE",
            12,
        ),
    ],
)
def test_year_requirements_the_parser_used_to_miss(description, status, minimum):
    state, found = years(description)
    assert state == status and found[0][0] == minimum


@pytest.mark.parametrize(
    "description",
    [
        "Salary:\n6+ years",  # a label that is not about experience
        "Benefits:\n5 years of annual leave accrual",
        "Location:\n3+ years",
    ],
)
def test_label_context_is_limited_to_experience_headings(description):
    assert years(description)[1] == []


def office(description, workplace_type="remote"):
    return workplace(job(workplace_type=workplace_type, description=description))


@pytest.mark.parametrize(
    "sentence",
    [
        "If the posting is specified as Onsite, you are required to work from an office.",
        "For candidates based in London, we use a hybrid model with 2 days in the office per week.",
        "For candidates based in London or Zurich we use a hybrid work model of 2 days in the office per week.",
        "Employees based in Berlin are expected to be on-site 3 days per week.",
    ],
)
def test_conditional_office_rules_cannot_exclude_a_job(sentence):
    decision = office(sentence)
    assert decision.status == "UNVERIFIED"
    assert any(e.rule == "conditional_office_attendance" for e in decision.evidence)


@pytest.mark.parametrize(
    "sentence",
    [
        "This is a hybrid role, with 3 days a week in the office.",
        "Employees are expected to be on-site 3 days per week (Monday, Tuesday, and Thursday).",
        "Hybrid with 3 days a week in the office",
    ],
)
def test_unconditional_office_attendance_still_excludes(sentence):
    assert office(sentence).status == "INELIGIBLE"


def test_a_conditional_clause_does_not_hide_an_unconditional_one():
    both = "For candidates based in London we offer free lunch.\nThis is a hybrid role, with 3 days a week in the office."
    assert office(both).status == "INELIGIBLE"


def restrictions(title="Software Engineer", description=""):
    return employment(job(title=title, description=description))


@pytest.mark.parametrize(
    "title,description",
    [
        ("Werkstudent (m/w/d)", "Build things."),
        ("Working Student - LLM Inference Performance", "Work on inference."),
        ("Remote / Pflichtpraktikum für Studierende - Medien", "Praktikum."),
        ("Intern", "Must be currently enrolled full-time at an accredited institution Must be 18."),
        (
            "Photonic Designer Intern",
            "Requirements Enrollment for the whole duration of the internship in a MSc in Physics.",
        ),
        (
            "Growth Intern",
            "You are currently enrolled in a top school business or engineering Masters degree programme.",
        ),
    ],
)
def test_student_only_roles_are_ineligible_for_a_graduate(title, description):
    decision = restrictions(title, description)
    assert decision.status == "INELIGIBLE"
    assert any(e.rule == "student_enrollment_required" for e in decision.evidence)


@pytest.mark.parametrize(
    "description",
    [
        "Enrollment in Doctolib's long-term employee value sharing plan is included.",
        "Enrolment onto our Virtual Stock Option programme: you will own a stake.",
        "Proactively identify opportunities to drive enrollment, adoption, and campaign deployment.",
        "Open to students currently enrolled in a university or recent graduates.",
        "Optional enrollment onto our Trusted Giving scheme.",
    ],
)
def test_benefit_and_marketing_enrollment_is_not_student_status(description):
    decision = restrictions("Backend Engineer", description)
    assert not any(e.rule == "student_enrollment_required" for e in decision.evidence)


@pytest.mark.parametrize(
    "description",
    [
        # company history, not a requirement
        "We've been doing it for over five years, teaching complete beginners with no prior tech background.",
        # residence years for a clearance
        "Active SC clearance (lived in the UK continuously for 5 years, background checks).",
        # recency window
        "You build working front-end prototypes or small features with AI tools, in the last 12 months.",
    ],
)
def test_years_that_are_not_experience_are_not_requirements(description):
    assert years(description)[1] == []


@pytest.mark.parametrize(
    "description",
    [
        "You are enrolled in a university or have recently graduated and are looking for experience.",
        "Currently enrolled in (or recently graduated from) a Bachelor's in Computer Science.",
        "Currently enrolled in University or recent fresh graduate looking to gain some work experience",
    ],
)
def test_roles_open_to_recent_graduates_are_not_student_only(description):
    decision = restrictions("Software Engineer Intern", description)
    assert not any(e.rule == "student_enrollment_required" for e in decision.evidence)


@pytest.mark.parametrize(
    "description",
    [
        "You'll start as a Junior Engineer and typically become an Engineer within 18-24 months.",
        "One of our SDRs moved into a leadership role running Sales Development within 8 months.",
        "Growing from 100+ people today to 200+ within 18 months.",
        "Please consider applying for a maximum of 3 roles within 12 months.",
    ],
)
def test_promotion_and_growth_timeframes_are_not_experience_minimums(description):
    assert years(description) == ("UNVERIFIED", [])


# --- provider location text such as Canonical's "Home based - Worldwide" ------------------------


def place(location, description="Build things."):
    posting = job(workplace_type="unknown", location=location, description=description)
    return geography(posting), workplace(posting)


@pytest.mark.parametrize(
    "location,geo,work",
    [
        (["Home based - Worldwide"], "ELIGIBLE", "ELIGIBLE"),
        (["Home based - Pakistan"], "ELIGIBLE", "ELIGIBLE"),
        (["Home Based - APAC"], "UNVERIFIED", "ELIGIBLE"),  # APAC may not include Pakistan
        (["Home Based - APAC; Home based - EMEA"], "UNVERIFIED", "ELIGIBLE"),
        (["Home based - EMEA"], "INELIGIBLE", "ELIGIBLE"),
        (["Home Based - Americas; Home based - EMEA"], "INELIGIBLE", "ELIGIBLE"),
        (["Home based - Germany"], "INELIGIBLE", "ELIGIBLE"),
        (["Berlin"], "UNVERIFIED", "UNVERIFIED"),
        (["Office Based - London, UK"], "UNVERIFIED", "UNVERIFIED"),
    ],
)
def test_home_based_location_text_states_a_hiring_region(location, geo, work):
    geography_result, workplace_result = place(location)
    assert (geography_result.status, workplace_result.status) == (geo, work)


def test_a_worldwide_location_does_not_override_an_explicit_exclusion():
    from job_scraper.screening import classify

    description = "We do not hire candidates based in Pakistan for this role."
    result, _ = place(["Home based - Worldwide"], description)
    assert result.status == "UNVERIFIED" and result.conflict  # the claims disagree
    posting = job(
        title="Python Engineer",
        workplace_type="unknown",
        location=["Home based - Worldwide"],
        description=description,
    )
    assert classify(posting).status == "INELIGIBLE"  # the explicit Pakistan exclusion decides


def test_worldwide_location_alone_never_makes_the_whole_job_eligible():
    from job_scraper.screening import classify

    posting = job(
        title="Python Engineer",
        workplace_type="unknown",
        location=["Home based - Worldwide"],
        description="Build Python services. Python experience required.",
    )
    assert classify(posting).status != "ELIGIBLE"  # employment restrictions stay unverified


@pytest.mark.parametrize(
    "location,geo",
    [
        (["Remote, Global"], "ELIGIBLE"),
        (["Remote, APAC"], "UNVERIFIED"),
        (["Remote, AMER"], "INELIGIBLE"),
        (["Remote, EMEA"], "INELIGIBLE"),
        (["Remote, Canada; Remote, United States"], "INELIGIBLE"),
        (["Remote - Australia"], "INELIGIBLE"),
        (["Remote, Pakistan"], "ELIGIBLE"),
        (["Remote - Hybrid"], "UNVERIFIED"),
        (["Remote, flexible hours"], "UNVERIFIED"),
        (["Remote"], "UNVERIFIED"),
        # a list of regions after "in" counts only when every label is a known region or country
        (["Remote in EU, Europe, North America"], "INELIGIBLE"),
        (["Remote in Germany"], "INELIGIBLE"),
        (["Remote in EU, Pakistan"], "ELIGIBLE"),
        (["Remote in Europe and Asia"], "UNVERIFIED"),
        (["Remote in the office"], "UNVERIFIED"),
        (["Remote in Europe, somewhere else"], "UNVERIFIED"),
    ],
)
def test_remote_region_location_text(location, geo):
    assert place(location)[0].status == geo


@pytest.mark.parametrize(
    "sentence,status",
    [
        ("Work from home with global travel twice a year for company events", "ELIGIBLE"),
        ("Location: we have remote Python engineering roles in every time zone.", "ELIGIBLE"),
        ("We have remote roles open in every time zone.", "ELIGIBLE"),
        # real overlap or hours wording still needs review
        ("Most members and your manager are in the same time zone.", "UNVERIFIED"),
        ("You must overlap with working hours in Berlin.", "UNVERIFIED"),
    ],
)
def test_canonical_wording_that_is_not_a_geography_or_time_zone_limit(sentence, status):
    result, _ = place(["Home based - Worldwide"], sentence)
    assert result.status == status


@pytest.mark.parametrize(
    "location,geo",
    [
        (["USA Only"], "INELIGIBLE"),
        (["Canada Only"], "INELIGIBLE"),
        (["Africa Only"], "INELIGIBLE"),
        (["LATAM"], "INELIGIBLE"),
        (["Europe"], "INELIGIBLE"),
        (["USA"], "INELIGIBLE"),
        (["Switzerland, Australia, Germany"], "INELIGIBLE"),
        (["Switzerland, Pakistan"], "ELIGIBLE"),
        (["Pakistan"], "ELIGIBLE"),
        (["Worldwide"], "ELIGIBLE"),
        (["Anywhere"], "ELIGIBLE"),
        (["APAC"], "UNVERIFIED"),
        (["Asia"], "UNVERIFIED"),
        (["Munich, Germany"], "UNVERIFIED"),  # a city is not a region label
        (["Remote"], "UNVERIFIED"),
        (["Time zone: CET (+/- 3 hours)"], "UNVERIFIED"),
        (["Your Remote Home Office"], "UNVERIFIED"),
    ],
)
def test_bare_region_or_country_location_text(location, geo):
    assert place(location)[0].status == geo


def test_a_bare_region_does_not_override_a_posting_that_says_anywhere():
    from job_scraper.screening import classify

    posting = job(
        title="Python Engineer",
        workplace_type="unknown",
        location=["USA Only"],
        description="Build Python services. We hire from anywhere in the world.",
    )
    assert classify(posting).status != "ELIGIBLE"


def test_changing_module_2_rules_does_not_stale_saved_judgments(monkeypatch):
    from job_scraper import matching, processing
    from job_scraper.config import MatchingConfig

    before = matching._rule_hash(MatchingConfig())
    monkeypatch.setattr(processing, "VERSION", "pakistan-0-3-vNEXT")
    assert matching._rule_hash(MatchingConfig()) == before
    assert matching.SCREENING_VERSION == "pakistan-0-3-v3.9"


@pytest.mark.parametrize(
    "description,geo",
    [
        ("Perks. Work remotely from anywhere in the world, subject to hours.", "UNVERIFIED"),
        ("Benefits: Work remotely from Anywhere, with flexibility.", "UNVERIFIED"),
        ("We are hiring worldwide for this role.", "UNVERIFIED"),
        # limits and company boilerplate are not openings: the bare country still excludes
        ("This role can be performed remotely from anywhere in Europe.", "INELIGIBLE"),
        ("Our customers are located worldwide. Join our team in the office.", "INELIGIBLE"),
        (
            "Benefits are globally designed and locally delivered. People from around the world.",
            "INELIGIBLE",
        ),
    ],
)
def test_bare_country_defers_to_a_posting_that_opens_the_role_to_anywhere(description, geo):
    from job_scraper.screening import classify

    posting = job(
        title="Python Engineer",
        workplace_type="unknown",
        location=["Switzerland"],
        description="Build Python services. Python experience required. " + description,
    )
    assert classify(posting).status == geo
