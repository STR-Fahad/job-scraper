"""Stable, lossless job-description items and checked model classifications."""

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .candidate import sanitize_text
from .evidence import (
    SKILL_ALIASES,
    EvidenceError,
    JobFinding,
    JobResponse,
    Segment,
    canonical_skill,
    minimum_years,
    resolve_job_requirements,
)

VERSION = "requirements-v2"
HEADINGS = {
    "requirements": "requirements",
    "required qualifications": "requirements",
    "minimum qualifications": "requirements",
    "qualifications": "qualifications",
    "responsibilities": "responsibilities",
    "what you'll do": "responsibilities",
    "nice to have": "preferred",
    "preferred qualifications": "preferred",
    "how it works": "process",
    "terms": "terms",
    "benefits": "benefits",
}
BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
TECH_ALIASES = {
    *[alias for aliases in SKILL_ALIASES.values() for alias in aliases],
    "C++",
    "C#",
    "Java",
    "Go",
    "Rust",
    "Linux",
    "PyTorch",
    "TensorFlow",
    "React",
    "Kubernetes",
    "AWS",
    "automated testing",
    "GraphQL",
    "Redis",
    "Node.js",
}
TECH = re.compile(
    r"(?<!\w)(?:"
    + "|".join(re.escape(x) for x in sorted(TECH_ALIASES, key=len, reverse=True))
    + r")(?!\w)",
    re.I,
)
RESTRICTION = re.compile(
    r"\b(?:remote|location|country|citizen|residen|visa|sponsor|authori[sz]ation|"
    r"based in|work from|office|hybrid)\b",
    re.I,
)
AMBIGUOUS = re.compile(r"\b(?:at least one|either|ideally|such as|or equivalent)\b", re.I)
SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "requirement_id": {"type": "string"},
                    "parts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {"type": "string"},
                            },
                            "required": ["kind"],
                        },
                    },
                },
                "required": ["requirement_id", "parts"],
            },
        }
    },
    "required": ["items"],
}


@dataclass(frozen=True)
class TechnologyMention:
    text: str
    canonical: str
    start: int
    end: int
    negated: bool

    def public(self) -> dict:
        return {
            "text": self.text,
            "canonical": self.canonical,
            "start": self.start,
            "end": self.end,
            "negated": self.negated,
        }


def extract_technologies(text: str) -> tuple[TechnologyMention, ...]:
    """Return explicit technology words only, with local span and negation evidence."""
    output = []
    for hit in TECH.finditer(text):
        label = hit.group()
        if label.casefold() == "go" and label != "Go":
            continue
        prefix = text[max(0, hit.start() - 45) : hit.start()]
        suffix = text[hit.end() : hit.end() + 35]
        negated = bool(
            re.search(r"\b(?:no|not|without|never)\b(?:\W+\w+){0,4}\W*$", prefix, re.I)
            or re.match(r"\s+(?:is\s+)?not\s+required\b", suffix, re.I)
        )
        output.append(
            TechnologyMention(label, canonical_skill(label), hit.start(), hit.end(), negated)
        )
    return tuple(output)


@dataclass(frozen=True)
class RequirementItem:
    id: str
    text: str
    section: str
    start: int
    end: int
    start_line: int
    end_line: int
    heading: bool = False

    @property
    def technologies(self) -> tuple[TechnologyMention, ...]:
        return extract_technologies(self.text)

    def public(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "section": self.section,
            "start": self.start,
            "end": self.end,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "heading": self.heading,
            "technologies": [x.public() for x in self.technologies],
        }


class ItemPart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["req", "pref", "exp", "duty", "eligibility", "nonreq", "unresolved"]


class ItemClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: str = Field(pattern=r"^R\d{3,}$")
    parts: list[ItemPart] = Field(min_length=1)


class ClassifiedRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ItemClassification]


def _heading(text: str) -> str | None:
    clean = re.sub(r"^[#*\s]+|[#:*\s]+$", "", text).casefold()
    return HEADINGS.get(clean)


def extract_requirements(description: str) -> list[RequirementItem]:
    """Preserve every nonempty logical source item and its exact substring offsets."""
    lines = description.splitlines(keepends=True)
    entries = []
    position = 0
    for number, line in enumerate(lines, 1):
        end = position + len(line)
        if line.strip():
            entries.append((number, position, end, line))
        position = end
    groups = []
    for entry in entries:
        number, start, end, raw = entry
        text = raw.strip()
        section = _heading(text)
        if groups:
            previous = groups[-1]
            old = previous[-1][3].rstrip()
            continued = (
                previous[-1][0] + 1 == number
                and not BULLET.match(raw)
                and section is None
                and _heading(previous[0][3].strip()) is None
                and (
                    (raw[:1].isspace() and not BULLET.match(raw))
                    or text[:1].islower()
                    or old.endswith((",", ";", "and", "or", "/"))
                )
            )
            if continued:
                previous.append(entry)
                continue
        groups.append([entry])
    output = []
    current_section = "overview"
    for group in groups:
        first, last = group[0], group[-1]
        raw = description[first[1] : last[2]].rstrip("\r\n")
        heading = _heading(raw.strip())
        if heading:
            current_section = heading
        output.append(
            RequirementItem(
                id=f"R{len(output) + 1:03d}",
                text=raw,
                section=current_section,
                start=first[1],
                end=first[1] + len(raw),
                start_line=first[0],
                end_line=last[0],
                heading=heading is not None,
            )
        )
    assert all(description[x.start : x.end] == x.text for x in output)
    return output


def resolve_classifications(
    response: ClassifiedRequirements,
    inventory: list[RequirementItem],
    profile: list[Segment],
    experience_years: float | None,
    catalog=None,
) -> list[dict]:
    by_id = {item.id: item for item in inventory}
    seen = set()
    findings = []
    for index, assignment in enumerate(response.items):
        field = f"items[{index}]"
        item = by_id.get(assignment.requirement_id)
        kinds = [part.kind for part in assignment.parts]

        def fail(
            location: str,
            reason: str,
            source_item: RequirementItem | None = item,
            source_id: str = assignment.requirement_id,
            source_kinds: tuple[str, ...] = tuple(kinds),
        ) -> EvidenceError:
            context = (
                sanitize_text(source_item.text).replace("\n", " ")[:120] if source_item else None
            )
            return EvidenceError(
                "requirement_validation",
                location,
                reason,
                requirement_id=source_id,
                classification=",".join(source_kinds),
                source_context=context or None,
            )

        if item is None:
            raise fail(field + ".requirement_id", "unknown_id")
        if item.id in seen:
            raise fail(field + ".requirement_id", "duplicate_id")
        seen.add(item.id)
        if len(kinds) != len(set(kinds)):
            raise fail(field + ".parts", "duplicate_kind")
        if "nonreq" in kinds and len(kinds) != 1:
            raise fail(field + ".parts", "contradictory_assignment")
        if item.heading and kinds != ["nonreq"]:
            raise fail(field, "heading_misclassified")
        if item.section == "requirements" and not item.heading and "nonreq" in kinds:
            raise fail(field, "mandatory_item_discarded")
        if item.section == "preferred" and "req" in kinds:
            raise fail(field, "preferred_marked_required")
        if minimum_years(item.text) is not None and "exp" not in kinds:
            raise fail(field, "experience_minimum_misclassified")
        if RESTRICTION.search(item.text) and "nonreq" in kinds and not item.heading:
            raise fail(field, "restriction_discarded")
        positive = [mention for mention in item.technologies if not mention.negated]
        if positive and "unresolved" in kinds and not AMBIGUOUS.search(item.text):
            raise fail(field, "clear_skill_marked_unresolved")
        if (
            positive
            and item.section in {"requirements", "preferred"}
            and not any(kind in {"req", "pref"} for kind in kinds)
            and "unresolved" not in kinds
        ):
            raise fail(field, "technical_item_misclassified")
        for part in assignment.parts:
            if part.kind in {"req", "pref"}:
                labels = list(dict.fromkeys(mention.text for mention in positive)) or [
                    item.text[:200]
                ]
                findings.extend(
                    JobFinding(kind=part.kind, text=label, job_evidence_id=item.id)
                    for label in labels
                )
            elif part.kind in {"exp", "duty", "eligibility", "unresolved"}:
                text = item.text[:200]
                if part.kind == "exp":
                    match = re.search(r"\d+(?:\.\d+)?\s*\+?\s*(?:years?|yrs?)", item.text, re.I)
                    if match:
                        text = match.group()
                findings.append(JobFinding(kind=part.kind, text=text, job_evidence_id=item.id))
    if seen != set(by_id):
        missing = sorted(set(by_id) - seen)[0]
        raise EvidenceError(
            "requirement_validation",
            "items",
            "missing_requirement_id",
            requirement_id=missing,
            source_context=sanitize_text(by_id[missing].text).replace("\n", " ")[:120],
        )
    segments = [Segment(x.id, f"description[{x.start_line}]", x.text) for x in inventory]
    return resolve_job_requirements(
        JobResponse(findings=findings), segments, profile, experience_years, catalog
    )
