"""Whole-month experience: conversion, display and comparison with job minimums.

Experience is held as an integer number of months; years and months are only a display.
Nothing here reads a calendar or adds up employment dates: an approved total is a
snapshot supplied by the user and is never recalculated or extended.
"""

import re
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation

from .screening import NUMBER_WORDS

# Number words are shared with Module 2 (`screening.NUMBER_WORDS`), so both read the same wording.
NUMBER = r"(?:\d+(?:\.\d+)?|\b(?:" + "|".join(NUMBER_WORDS) + r")\b)"
UNIT = r"(?P<unit>years?|yrs?|months?|mths?)"
PLUS = r"(?:\s*\+|\s+plus\b)?"
COMPOSITE = re.compile(
    r"\b(?P<years>\d+)\s*(?:years?|yrs?)\s*(?:and\s*|,\s*)?(?P<months>\d+)\s*(?:months?|mths?)\b",
    re.I,
)
RANGE = re.compile(
    rf"[\[(]?\s*\b(?P<low>{NUMBER})\s*(?:-|–|to)\s*(?P<high>{NUMBER}){PLUS}\s*[\])]?\s*"
    rf"(?:or more\s+)?{UNIT}\b",
    re.I,
)
SINGLE = re.compile(
    rf"[\[(]?\s*\b(?P<low>{NUMBER})(?:\s*\(\d+\))?{PLUS}\s*[\])]?\s*(?:or more\s+)?{UNIT}\b",
    re.I,
)
# Months name many things besides experience (a 6 month contract, a 3 month probation), so a
# month quantity counts as an experience requirement only with experience wording nearby.
EXPERIENCE_WORDS = re.compile(
    r"\b(?:experience|experienced|professional(?:ly)?|hands[- ]on|industry|commercial(?:ly)?|"
    r"working|software|engineering|development)\b",
    re.I,
)
LEADS_AWAY = re.compile(r"\b(?:within|after|every|per|each|first|next)\W+(?:\w+\W+){0,1}$", re.I)
FOLLOWS_AWAY = re.compile(
    r"^\W*(?:\w+\W+){0,2}?(?:contract|probation(?:ary)?|notice|trial|internship|fixed[- ]term|"
    r"warranty|onboarding|period|term)\b",
    re.I,
)
# Durations that describe something other than the candidate's experience (any unit).
UNRELATED_AFTER = re.compile(
    r"^\W*(?:\w+\W+){0,3}?(?:sales\s+cycles?|customer\s+relationships?|company\s+history|"
    r"product\s+(?:age|history)|ago|old)\b",
    re.I,
)
# A duration the employer promises ("a 10 year enterprise security commitment", "up to 15 years of
# security and maintenance commitments for LTS releases") is not a candidate requirement.
COMMITMENT_AFTER = re.compile(
    r"^\W*(?:of\W+)?(?:\w+\W+){0,5}?(?:commitments?|guarantees?|support\s+(?:window|lifecycle|period))\b",
    re.I,
)
# A time window, not an experience minimum: "shaped the internet for the better over the last 25
# years", "shaping the next 25 years of technology", "in the past 12 months". Company boilerplate reads
# like this.
TIME_WINDOW_BEFORE = re.compile(
    r"\bthe\s+(?:last|past|previous|preceding|next|coming|following)\s*$", re.I
)
MAXIMUM = re.compile(r"\b(?:at most|up to|maximum(?: of)?|no more than|under|less than)\s*$", re.I)
PREFERRED = re.compile(
    r"\b(?:preferred|nice[- ]to[- ]have|desirable|bonus|ideally|a plus|not required|optional)\b",
    re.I,
)
AMBIGUOUS = re.compile(r"\b(?:or equivalent|alternatively|unless|or\s+\d)\b", re.I)
LEADING_PREFERENCE = re.compile(
    r"^\W*(?:ideally|preferably|preferred|nice[- ]to[- ]have|bonus|desirable)\b", re.I
)


def _amount(value: str) -> str:
    """Digits stay as written; a number word becomes its digits."""
    word = value.strip().casefold()
    return str(NUMBER_WORDS[word]) if word in NUMBER_WORDS else value


def _boundary(text: str, position: int, side: str, marks: str) -> int:
    """Start (left) or end (right) of the phrase around a position."""
    if side == "left":
        return max((text.rfind(mark, 0, position) for mark in marks), default=-1) + 1
    found = [i for mark in marks if (i := text.find(mark, position)) >= 0]
    return min(found) if found else len(text)


def with_kind(requirement: "Requirement", kind: str) -> "Requirement":
    return replace(requirement, kind=kind)


def months_from_years(value) -> tuple[int, bool]:
    """Decimal years to whole months. Exact when years x 12 is a whole number; otherwise
    rounded DOWN (never up, so no experience is invented) and reported as inexact."""
    try:
        months = Decimal(str(value)) * 12
    except InvalidOperation as exc:
        raise ValueError(f"not a number of years: {value!r}") from exc
    if months < 0:
        raise ValueError("experience cannot be negative")
    whole = int(months.to_integral_value(rounding="ROUND_FLOOR"))
    return whole, months == whole


def plural(count: int, unit: str) -> str:
    return f"{count} {unit}" + ("" if count == 1 else "s")


def format_months(months) -> str | None:
    """6 -> '6 months', 12 -> '1 year', 15 -> '1 year, 3 months'. Unknown stays unknown."""
    if months is None:
        return None
    years, rest = divmod(int(months), 12)
    if not years:
        return plural(rest, "month")
    if not rest:
        return plural(years, "year")
    return f"{plural(years, 'year')}, {plural(rest, 'month')}"


@dataclass(frozen=True)
class Requirement:
    minimum_months: int
    exact: bool
    kind: str  # minimum | range | preferred | maximum | ambiguous
    maximum_months: int | None = None
    quote: str = ""

    def public(self) -> dict:
        return {
            "required_months": self.minimum_months,
            "required_display": format_months(self.minimum_months),
            "requirement_kind": self.kind,
            "converted_exactly": self.exact,
        }


def _to_months(value: str, unit: str) -> tuple[int, bool]:
    amount = Decimal(value) * (12 if unit.lower().startswith("y") else 1)
    whole = int(amount.to_integral_value(rounding="ROUND_CEILING"))
    # A minimum is rounded UP: a whole-month candidate meets 15.6 months only with 16.
    return whole, amount == whole


def parse_requirement(text: str) -> Requirement | None:
    """First numeric experience quantity in a requirement, as whole months.

    Year quantities keep the long-standing rule (any 'N years'); month quantities need
    experience wording and must not look like a contract or probation length."""
    candidates = []
    composite = COMPOSITE.search(text)
    if composite:
        months = int(composite["years"]) * 12 + int(composite["months"])
        candidates.append((composite.start(), months, True, composite, None))
    for pattern in (RANGE, SINGLE):
        for hit in pattern.finditer(text):
            unit = hit["unit"].lower()
            after, before = text[hit.end() :], text[: hit.start("low")]
            if UNRELATED_AFTER.search(after) or COMMITMENT_AFTER.search(after):
                continue
            if TIME_WINDOW_BEFORE.search(before):
                continue
            # Word and bracket forms ("five years", "[3+] years") need experience wording and
            # no time-frame wording, like month quantities; plain digit years keep their rule.
            extended = not hit["low"][0].isdigit() or hit.group().lstrip().startswith(("[", "("))
            away = LEADS_AWAY.search(before) or FOLLOWS_AWAY.search(after)
            if (unit.startswith(("month", "mth")) or extended) and (
                not EXPERIENCE_WORDS.search(text) or away
            ):
                continue
            low, exact = _to_months(_amount(hit["low"]), unit)
            high = _to_months(_amount(hit["high"]), unit)[0] if "high" in hit.groupdict() else None
            candidates.append((hit.start(), low, exact, hit, high))
    if not candidates:
        return None
    start, months, exact, hit, high = min(candidates, key=lambda c: c[0])
    end = hit.end()
    phrase = text[
        _boundary(text, start, "left", ".;\n,()") : _boundary(text, end, "right", ".;\n,()")
    ]
    sentence = text[_boundary(text, start, "left", ".;\n") : _boundary(text, end, "right", ".;\n")]
    if MAXIMUM.search(text[:start]):
        kind = "maximum"
    elif PREFERRED.search(phrase) or LEADING_PREFERENCE.match(sentence):
        # Only the wording around THIS quantity counts: "3 years required, Python preferred"
        # and "3+ years (5 preferred)" state a mandatory 3 years.
        kind = "preferred"
    elif AMBIGUOUS.search(sentence):
        kind = "ambiguous"
    elif high is not None and high != months:
        kind = "range"
    else:
        kind = "minimum"
    return Requirement(months, exact, kind, high if kind == "range" else None, hit.group().strip())


def minimum_months(text: str) -> int | None:
    found = parse_requirement(text)
    return found.minimum_months if found else None


def compare(required: Requirement, approved_months: int | None) -> dict:
    """Candidate against one requirement. Unknown experience is never shown as zero."""
    view = {
        **required.public(),
        "approved_months": approved_months,
        "approved_display": format_months(approved_months),
    }
    if approved_months is None:
        return {**view, "gap_months": None, "gap_display": None, "met": None}
    gap = max(0, required.minimum_months - approved_months)
    return {
        **view,
        "gap_months": gap,
        "gap_display": "none" if not gap else format_months(gap),
        "met": approved_months >= required.minimum_months,
    }


def requirement_in_context(quote: str, source: str) -> Requirement | None:
    """The requirement a finding quotes, read with the wording around it in the source.

    A month quantity needs experience wording, and a preferred or maximum level needs the
    words before the number, so the bare quote ('6 months', '5 years') cannot be judged
    alone. A vague finding, which quotes no quantity, gets no number."""
    in_context = parse_requirement(source)
    if in_context and in_context.quote.strip() == quote.strip():
        return in_context
    return parse_requirement(quote)


# Years attached to one skill or one area of work are not total experience: "Strong Haskell
# skills with at least 3 years" or "3+ years of experience building and operating cloud
# infrastructure". They are scoped to that skill or area and never decide on their own.
_GENERIC_SKILL = (
    r"software|engineering|programming|technical|coding|development|problem|communication|"
    r"interpersonal|leadership|management|professional"
)
SKILL_BEFORE = re.compile(
    rf"\b(?!(?:{_GENERIC_SKILL})\b)[\w+#.-]+\s+(?:skills?|expertise|proficiency|knowledge)"
    r"\s+(?:with|of)?\s*(?:at least|over|more than)?\s*$",
    re.I,
)
DOMAIN_AFTER = re.compile(
    r"^\s*of\s+(?:[\w-]+\s+){0,2}?experience\s+(?:in|with|building|operating|developing|"
    r"designing|working|using|managing|running|deploying|maintaining|leading)\b"
    r"(?P<rest>[^.;\n]{0,80})",
    re.I,
)
# "3+ years in SRE, platform or infrastructure roles": the years belong to a named specialist area,
# not to total experience. Only a recognized specialist area counts, and any generic software
# wording in the same phrase ("in software engineering roles") keeps it a total-experience minimum.
AREA_AFTER = re.compile(
    r"^\s*(?:of\s+(?:[\w-]+\s+){0,2}?experience\s+)?in\s+(?:an?\s+|the\s+)?"
    r"(?P<rest>[^.;\n]{0,80})",
    re.I,
)
SPECIALIST_AREA = re.compile(
    r"\b(?:sre|site reliability|devops|platform|infrastructure|security|network(?:ing)?|"
    r"data|analytics|machine learning|embedded|firmware|mobile|ios|android|cloud|qa|"
    r"quality assurance|support|sales|consulting|marketing|product management)\b",
    re.I,
)
GENERIC_OBJECT = re.compile(
    r"\b(?:software|web|full.?stack|front.?end|back.?end|applications?|products?|production|"
    r"engineering|development)\b",
    re.I,
)


def scoped_years_text(excerpt: str, quote: str) -> bool:
    """True when the years in `quote` qualify a named skill or an area of work."""
    position = excerpt.find(quote) if quote else -1
    if position < 0:
        return False
    if SKILL_BEFORE.search(excerpt[max(0, position - 60) : position]):
        return True
    tail = excerpt[position + len(quote) : position + len(quote) + 140]
    after = DOMAIN_AFTER.match(tail)
    if after and not GENERIC_OBJECT.search(after["rest"][:60]):
        return True
    area = AREA_AFTER.match(tail)
    return bool(
        area
        and SPECIALIST_AREA.search(area["rest"][:60])
        and not GENERIC_OBJECT.search(area["rest"][:60])
    )
