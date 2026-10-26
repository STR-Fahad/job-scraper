"""Exact ISO country lookups. No fuzzy geocoding or invented country codes."""

import re

import pycountry

ALIASES = {
    "uk": "GB",
    "u.k.": "GB",
    "u.s.": "US",
    "u.s.a.": "US",
    "south korea": "KR",
    "north korea": "KP",
    "russia": "RU",
    "vietnam": "VN",
    "turkey": "TR",
    "iran": "IR",
    "taiwan": "TW",
}
# Only regions unambiguously excluding Pakistan are negative. EMEA/APAC and
# company-defined territories stay unknown rather than implying a hiring right.
EXCLUDED_REGIONS = {
    "eu",
    "european union",
    "eea",
    "european economic area",
    "europe",
    "north america",
    "south america",
    "latin america",
}
GLOBAL = {"worldwide", "anywhere", "global", "anywhere in the world", "all countries"}


def country_code(value):
    name = value.strip()
    if name.casefold() in ALIASES:
        return ALIASES[name.casefold()]
    if name.casefold() + "." in ALIASES:
        return ALIASES[name.casefold() + "."]
    # ISO alpha-2/alpha-3/name only: numeric and fuzzy matches are not accepted.
    try:
        country = pycountry.countries.lookup(name)
    except LookupError:
        return None
    if name.casefold() in {
        str(getattr(country, key, "")).casefold()
        for key in ("name", "official_name", "common_name", "alpha_2", "alpha_3")
    }:
        return country.alpha_2
    return None


def territory(value):
    """Return explicit eligibility for an entire country/region label, never a prefix."""
    label = re.sub(r"^the\s+", "", value.strip().rstrip(".!"), flags=re.I)
    folded = label.casefold()
    if folded in GLOBAL:
        return "ELIGIBLE"
    if folded in EXCLUDED_REGIONS:
        return "INELIGIBLE"
    code = country_code(label)
    return "ELIGIBLE" if code == "PK" else "INELIGIBLE" if code else "UNVERIFIED"


def location_country(location):
    """Only explicit country suffixes; do not guess cities or interpret prose codes."""
    for part in reversed(location.split(",")):
        code = country_code(part)
        if code:
            return code
    return None
