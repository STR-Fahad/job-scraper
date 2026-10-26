"""Evidence-based screening rules for a Pakistan-based, 0–3-year target profile.

English rules are deliberately bounded. Unrecognized wording remains unknown;
country eligibility never establishes citizenship or employment authorization.
"""

import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from .countries import location_country, territory
from .models import Job

Status = Literal["ELIGIBLE", "INELIGIBLE", "UNVERIFIED"]
CATEGORIES = {
    "backend": r"\bback[ -]?end\b",
    "full-stack": r"\bfull[ -]?stack\b",
    "Rails": r"\b(?:ruby on rails|rails)\b",
    "Python": r"\bpython\b",
    "AI/ML": r"\b(?:AI/ML|machine learning|artificial intelligence|ML engineer|AI engineer)\b",
    "LLM": r"\b(?:LLMs?|large language models?|generative AI)\b",
    "data engineering": r"\bdata engineer(?:ing)?\b",
    "DevOps": r"\b(?:devops|site reliability|SRE|platform engineer(?:ing)?)\b",
}


class Evidence(BaseModel):
    field: str
    quote: str
    rule: str


class Decision(BaseModel):
    status: Status = "UNVERIFIED"
    evidence: list[Evidence] = Field(default_factory=list)
    reason: str


class Classification(BaseModel):
    status: Status
    categories: list[str]
    workplace_type: str
    employment_types: list[str]
    experience_requirements: list[dict]
    decisions: dict[str, Decision]


def finding(status, quote, rule, field="description", reason="Advertised condition"):
    return Decision(
        status=status, reason=reason, evidence=[Evidence(field=field, quote=quote, rule=rule)]
    )


def unknown(reason):
    return Decision(reason=reason)


def agree(items, reason):
    """Claims about the SAME fact must agree. Conflicts do not become exclusion."""
    states = {item.status for item in items}
    state = next(iter(states)) if len(states) == 1 else "UNVERIFIED"
    return Decision(
        status=state, reason=reason, evidence=[e for item in items for e in item.evidence]
    )


def requirements(items, reason):
    """Independent requirements are conjunctive: one definite blocker suffices."""
    states = {item.status for item in items}
    state = (
        "INELIGIBLE"
        if "INELIGIBLE" in states
        else "UNVERIFIED"
        if not states or "UNVERIFIED" in states
        else "ELIGIBLE"
    )
    return Decision(
        status=state, reason=reason, evidence=[e for item in items for e in item.evidence]
    )


def clauses(text):
    # Do not split decimals, U.S. abbreviations or quoted year possessives.
    return [
        part.strip(" \t•●-")
        for part in re.split(r"[\n;]+|(?<=[.!?])\s+(?=[A-Z])", text)
        if part.strip()
    ]


PROHIBITED_PK = re.compile(
    r"\b(?:cannot|can't|may not|do not|not able to)\s+(?:hire|employ|engage|accept|work from)"
    r"(?:\s+(?:anyone|people|applicants|candidates|contractors))?"
    r"(?:\s+(?:working|based|located))?(?:\s+(?:in|from))?\s+Pakistan\b"
    r"|\b(?:excluding|except) Pakistan\b",
    re.I,
)


def candidate_territory(value):
    quote = json.dumps(value, ensure_ascii=False)
    if re.search(r"\b(?:except|excluding|only if|subject to)\b", quote, re.I):
        return "UNVERIFIED"
    if isinstance(value, str) and territory(value) != "UNVERIFIED":
        return territory(value)
    labels = value if isinstance(value, list) else re.split(r"[,;/]|\band\b", str(value))
    states = []
    for label in labels:
        if isinstance(label, dict):
            # Contradictory code/name pairs are not silently resolved by priority.
            values = [str(label[k]) for k in ("alpha2", "name") if label.get(k)]
            answers = {territory(v) for v in values}
            states.append(next(iter(answers)) if len(answers) == 1 else "UNVERIFIED")
        else:
            states.append(territory(str(label)))
    # A territory list is a set of alternatives, not simultaneous restrictions.
    return (
        "ELIGIBLE"
        if "ELIGIBLE" in states
        else "INELIGIBLE"
        if states and set(states) == {"INELIGIBLE"}
        else "UNVERIFIED"
    )


def geography(job):
    territorial, schedules = [], []
    for key in ("locationRestrictions", "candidate_required_location"):
        value = job.restrictions.get(key)
        if value:
            territorial.append(
                finding(
                    candidate_territory(value),
                    json.dumps(value),
                    "candidate_territory",
                    f"restrictions.{key}",
                )
            )
    for clause in clauses(job.description):
        if re.search(r"\b(?:customers?|clients?|users?|products?)\b", clause, re.I):
            continue
        state = None
        if PROHIBITED_PK.search(clause):
            state = "INELIGIBLE"
        elif re.search(r"\b(?:not|no|unless|except|excluding)\b", clause, re.I):
            if re.search(r"\b(?:remote|work from|hire|based|located)\b", clause, re.I):
                state = "UNVERIFIED"
        else:
            patterns = [
                r"^(?:(?:you|candidates|applicants) (?:can |may |will )?)?work (?:remotely )?from (.+)",
                r"\b(?:must (?:be (?:based|located)|reside|live)|(?:only|exclusively) (?:based|located)) in (.+)",
                r"\b(?:open to|accepting) (?:candidates|applicants|people) (?:based |located )?(?:in|from) (.+)",
                r"\bwe (?:can |do )?hire (?:candidates )?(?:in|from) (.+)",
                r"\b(?:fully )?remote within (.+)",
                r"\b(?:this (?:role|position) (?:is|will be) )based remotely in (.+)",
                r"^(?:this (?:role|position) is )?remote (worldwide.*)",
            ]
            for pattern in patterns:
                match = re.search(pattern, clause, re.I)
                if match:
                    label = match[1].strip().rstrip(".! ")
                    # Consume the restricted suffix before interpreting 'anywhere'.
                    label = re.sub(r"^anywhere (?:in|within) (?:the )?", "", label, flags=re.I)
                    if label.casefold() == "world":
                        label = "worldwide"
                    state = territory(label)
                    break
            if state is None:
                match = re.fullmatch(r"(.+?)[- ]only[.!]?", clause, re.I)
                if match:
                    state = territory(match[1])
                elif re.fullmatch(r"worldwide remote[.!]?", clause, re.I):
                    state = "ELIGIBLE"
        if state:
            territorial.append(finding(state, clause, "explicit_geography"))
        if re.search(r"\b(?:time[ -]?zone|overlap|working hours|business hours)\b", clause, re.I):
            schedules.append(finding("UNVERIFIED", clause, "schedule_review"))
    if job.workplace_type in {"onsite", "hybrid"} and job.location:
        codes = [location_country(v) for v in job.location]
        state = "ELIGIBLE" if "PK" in codes else "INELIGIBLE" if all(codes) else "UNVERIFIED"
        territorial.append(
            finding(state, json.dumps(job.location), "attendance_country", "location")
        )
    if job.restrictions.get("timezoneRestrictions"):
        schedules.append(
            finding(
                "UNVERIFIED",
                json.dumps(job.restrictions["timezoneRestrictions"]),
                "schedule_review",
                "restrictions.timezoneRestrictions",
            )
        )
    country = agree(
        territorial, "Territory claims must agree; missing or contradictory geography needs review"
    )
    states = {item.status for item in territorial}
    if "INELIGIBLE" in states and "ELIGIBLE" not in states:
        country.status = "INELIGIBLE"
    return requirements(
        [country, *schedules], "Country eligibility and schedule are independent requirements"
    )


OPTIONAL = re.compile(
    r"\b(?:preferred|nice[- ]to[- ]have|desirable|bonus|ideally|not required|optional)\b", re.I
)
YEARS = re.compile(
    r"(?P<low>\d+(?:\.\d+)?)\s*(?:(?:-|–|to)\s*(?P<high>\d+(?:\.\d+)?))?"
    r"\s*\+?\s*(?P<unit>years?|months?)(?:['’])?\b",
    re.I,
)
CONTEXT = re.compile(
    r"\b(?:experience|engineering|development|python|java|ruby|rails|react|software|programming|sales|recruiting|leadership|management)\b",
    re.I,
)


def experience(job):
    findings, extracted = [], []
    optional_section = False
    credits = [
        c
        for c in clauses(job.description)
        if re.search(
            r"\b(?:degree|master'?s|bachelor'?s|ph\.?d)\b.*\b(?:count as|substitute for|equivalent to|replace|credited as)\b.*\b(?:years?|months?|experience)\b",
            c,
            re.I,
        )
    ]
    for clause in clauses(job.description):
        if re.fullmatch(
            r"(?:preferred (?:qualifications|skills)|nice[- ]to[- ]haves?|bonus(?: points| skills| qualifications)?|desirable(?: skills)?)[\s:]*",
            clause,
            re.I,
        ):
            optional_section = True
            continue
        if re.fullmatch(
            r"(?:requirements|required(?: qualifications| skills)?|minimum qualifications|must[- ]haves?|what you (?:bring|need)|essential(?: skills)?)[\s:]*",
            clause,
            re.I,
        ):
            optional_section = False
            continue
        if clause in credits or re.search(
            r"\b(?:our (?:company|team|founder|CEO|CTO)|company has|we have|combined experience)\b",
            clause,
            re.I,
        ):
            continue
        if re.search(
            r"\bno (?:\w+\s+){0,6}experience (?:is )?(?:required|necessary)\b", clause, re.I
        ):
            findings.append(finding("ELIGIBLE", clause, "no_experience_required"))
            continue
        # 'Ideally' can modify a second qualification without weakening the first.
        parts = re.split(r",\s*(?=(?:ideally|preferably|prefer(?:red|ably))\b)", clause, flags=re.I)
        for part in parts:
            matches = list(YEARS.finditer(part))
            if not matches or not CONTEXT.search(part):
                continue
            alternatives = any(
                re.search(
                    r"\bor\s+(?:(?:at least|minimum of)\s+)?$",
                    part[left.end() : right.start()],
                    re.I,
                )
                for left, right in zip(matches, matches[1:], strict=False)
            )
            for match in matches:
                scale = 12 if match["unit"].lower().startswith("month") else 1
                low = float(match["low"]) / scale
                high = float(match["high"]) / scale if match["high"] else None
                maximum = bool(
                    re.search(
                        r"\b(?:at most|up to|maximum(?: of)?|no more than)\s*$",
                        part[: match.start()],
                        re.I,
                    )
                )
                if maximum:
                    low, high = 0, high if high is not None else low
                optional = optional_section or bool(OPTIONAL.search(part))
                uncertain = (
                    alternatives
                    or bool(re.search(r"\b(?:or equivalent|alternatively|unless)\b", part, re.I))
                    or (high is not None and high < low)
                )
                # A degree may satisfy some required years, but no candidate degree
                # is known. Preserve its evidence, never subtract guessed credits.
                degree_sensitive = low > 3 and bool(credits)
                state = (
                    "UNVERIFIED"
                    if uncertain or degree_sensitive
                    else "INELIGIBLE"
                    if low > 3
                    else "ELIGIBLE"
                )
                extracted.append(
                    dict(
                        minimum_years=low,
                        maximum_years=high,
                        optional=optional,
                        quote=clause,
                        degree_credit_evidence=credits if degree_sensitive else [],
                    )
                )
                if not optional:
                    findings.append(finding(state, clause, "experience_years"))
                    if degree_sensitive:
                        findings.extend(
                            finding("UNVERIFIED", c, "degree_credit_review") for c in credits
                        )
    return requirements(
        findings,
        "Each required minimum/maximum must fit 0–3 years; alternatives and degree credit need review",
    ), extracted


def relevance(job):
    title = job.title
    excluded_role = bool(
        re.search(
            r"\b(?:sales|mechanical|civil|electrical|chemical|manufacturing|accountant|recruiter|marketing)\b",
            title,
            re.I,
        )
    )
    engineering = (
        bool(re.search(r"\b(?:engineer|developer|programmer|devops|SRE)\b", title, re.I))
        and not excluded_role
    )
    software_title = bool(
        re.search(
            r"\b(?:software|back[ -]?end|full[ -]?stack|python|rails|data|ML|AI|LLM|devops|platform|site reliability)\b",
            title,
            re.I,
        )
    )
    matches, items = [], []
    for name, pattern in CATEGORIES.items():
        title_match = re.search(pattern, title, re.I)
        if engineering and title_match:
            matches.append(name)
            items.append(Evidence(field="title", quote=title_match[0], rule=f"category:{name}"))
            continue
        for clause in clauses(job.description):
            if not engineering or re.search(
                r"\b(?:not|no|without|other (?:roles|positions)|sell|selling|customers?|clients?|our company|our platform)\b",
                clause,
                re.I,
            ):
                continue
            match = re.search(pattern, clause, re.I)
            action = re.search(
                r"\b(?:build|develop|implement|design|maintain|deploy|engineer|write|program)\w*\b",
                clause,
                re.I,
            )
            skill = re.search(
                r"\b(?:experience|proficien\w*|skills?|expertise|required|fundamentals|familiarity)\b",
                clause,
                re.I,
            )
            if match and (action or (software_title and skill)):
                matches.append(name)
                items.append(Evidence(field="description", quote=clause, rule=f"category:{name}"))
                break
    return matches, Decision(
        status="ELIGIBLE" if matches else "UNVERIFIED",
        evidence=items,
        reason="Relevant engineering role and affirmative role-specific technology evidence required",
    )


def employment(job):
    groups = {
        "citizenship": [],
        "authorization": [],
        "residency": [],
        "sponsorship": [],
        "employment": [],
        "pakistan_engagement": [],
    }
    blanket = []
    pk_engagement = []
    for field, text in (
        ("description", job.description),
        ("restrictions", json.dumps(job.restrictions)),
    ):
        for clause in clauses(text):
            if PROHIBITED_PK.search(clause):
                groups["pakistan_engagement"].append(
                    finding("INELIGIBLE", clause, "pakistan_employment_prohibited", field)
                )
                continue
            clear = re.fullmatch(
                r"(?:There are )?No (?:citizenship,? (?:or |and )?residency (?:or |and )?)?(?:work authorization|employment) restrictions[.!]?",
                clause,
                re.I,
            )
            if clear:
                blanket.append(
                    finding("ELIGIBLE", clause, "explicit_unrestricted_employment", field)
                )
                continue
            if re.search(
                r"\b(?:can|are able to) (?:legally )?(?:hire|employ|engage)\b.*\b(?:in|from) Pakistan\b",
                clause,
                re.I,
            ):
                pk_engagement.append(finding("ELIGIBLE", clause, "pakistan_engagement", field))
                groups["pakistan_engagement"].append(pk_engagement[-1])
            patterns = {
                "citizenship": r"\bcitizen(?:ship|s)?\b",
                "authorization": r"\b(?:work (?:authorization|permit)|(?:authori[sz]ed|eligible|right) to work)\b",
                "residency": r"\bresiden(?:cy|ts?)\b",
                "sponsorship": r"\b(?:sponsor(?:ship)?|visa)\b",
                "employment": r"\b(?:contractor|employees? only|W-?2|1099|C2C|employment restrictions|security clearance)\b",
            }
            for name, pattern in patterns.items():
                if not re.search(pattern, clause, re.I):
                    continue
                waiver = (
                    name == "citizenship"
                    and re.search(
                        r"\b(?:regardless of citizenship|any citizenship|all citizenships|no citizenship restrictions)\b",
                        clause,
                        re.I,
                    )
                    or name == "residency"
                    and re.search(r"\bno residency restrictions\b", clause, re.I)
                    or name == "authorization"
                    and re.search(
                        r"\b(?:no (?:foreign )?work (?:authorization|permit) (?:is )?required|(?:foreign )?work authorization is not required)\b",
                        clause,
                        re.I,
                    )
                )
                groups[name].append(
                    finding("ELIGIBLE" if waiver else "UNVERIFIED", clause, f"{name}_review", field)
                )
    # Explicit complementary facts can resolve eligibility without a magic
    # phrase: permitted Pakistan engagement, citizenship neutrality and no foreign
    # authorization requirement. A mere geographic invitation cannot do this.
    independent = [agree(values, f"{name} evidence") for name, values in groups.items() if values]
    if blanket:
        independent.append(agree(blanket, "Explicit general employment permission"))
    else:
        for name in ("citizenship", "authorization"):
            if not groups[name]:
                independent.append(unknown(f"Candidate {name} is not established"))
        if not pk_engagement:
            independent.append(unknown("Hiring arrangement for Pakistan is not established"))
        else:
            independent.append(agree(pk_engagement, "Explicit Pakistan engagement permission"))
    return requirements(
        independent,
        "Employment, citizenship, residency and authorization constraints are independently applicable",
    )


def classify(job: Job) -> Classification:
    matches, category = relevance(job)
    exp, extracted = experience(job)
    decisions = {
        "category": category,
        "experience": exp,
        "geography": geography(job),
        "restrictions": employment(job),
        "workplace": finding(
            "UNVERIFIED" if job.workplace_type == "unknown" else "ELIGIBLE",
            job.workplace_type,
            "normalized_workplace",
            "workplace_type",
        ),
    }
    status = requirements(
        list(decisions.values()), "All independent screening dimensions apply"
    ).status
    return Classification(
        status=status,
        categories=matches,
        workplace_type=job.workplace_type,
        employment_types=job.employment_type,
        experience_requirements=extracted,
        decisions=decisions,
    )
