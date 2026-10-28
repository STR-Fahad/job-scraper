"""Deterministic source references and conservative validation of model findings."""

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EVIDENCE_VERSION = "evidence-v7"
MAX_SEGMENT_CHARS = 420
KINDS = ("req", "pref", "exp", "duty", "match", "transfer", "missing", "eligibility", "unresolved")
SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "text": {"type": "string"},
                    "job_evidence_id": {"type": "string"},
                },
                "required": [
                    "kind",
                    "text",
                    "job_evidence_id",
                ],
            },
        }
    },
    "required": ["findings"],
}
YEAR_RANGE = re.compile(r"\b(\d+(?:\.\d+)?)\s*[-–]\s*\d+(?:\.\d+)?\s*(?:years?|yrs?)\b", re.I)
YEARS = re.compile(r"\b(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)\b", re.I)
TECH_TERMS = re.compile(
    r"\b(?:python|ruby|rails|sql|api|apis|react|typescript|javascript|docker|linux|git|"
    r"testing|tests|ai|llm|model|models|code|coding|repository|repositories|backend|"
    r"frontend|data|cloud|aws|kubernetes|devops|fastapi|django|flask)\b",
    re.I,
)
SKILL_ALIASES = {
    "python": ("python",),
    "ruby": ("ruby",),
    "ruby on rails": ("ruby on rails", "rails", "rails 8"),
    "docker": ("docker",),
    "git": ("git",),
    "sql": ("sql",),
    "rest api": ("rest api", "rest apis", "restful api", "restful apis"),
    "javascript": ("javascript", "js"),
    "typescript": ("typescript", "ts"),
    "postgresql": ("postgresql", "postgres"),
    "machine learning": ("machine learning", "ml"),
    "large language models": ("large language models", "large language model", "llms", "llm"),
}
ALIAS_TO_SKILL = {
    normalize: canonical for canonical, aliases in SKILL_ALIASES.items() for normalize in aliases
}
DEMONSTRATION = re.compile(
    r"\b(?:built|build|developed|develop|implemented|implement|used|using|"
    r"worked with|integrated|integrate|maintained|maintain|designed|design|"
    r"created|create|deployed|deploy|architected|trained|fine.tuned)\b",
    re.I,
)
PROJECT_ACTION = re.compile(
    r"\b(?:built|created|developed|implemented|deployed|designed|integrated|"
    r"trained|fine.tuned|benchmarked|curated|optimized)\b",
    re.I,
)
PROJECT_RELEVANCE = {
    "python": re.compile(
        r"\b(?:python|code|script|model|pipeline|pre.processing|dataset|resnet|lstm|cnn)\b", re.I
    ),
    "pytorch": re.compile(r"\b(?:pytorch|model|training|fine.tun|resnet|lstm|cnn|gpu)\b", re.I),
    "tensorflow": re.compile(r"\b(?:tensorflow|model|training|resnet|lstm|cnn|gpu)\b", re.I),
    "ruby on rails": re.compile(r"\b(?:rails|backend|api|webhook|server|application)\b", re.I),
    "react": re.compile(r"\b(?:react|frontend|ui|component|interface|state handling)\b", re.I),
}
PROJECT_FIELD = re.compile(r"^projects\[(\d+)\]\.text\[(\d+)\]$")


class EvidenceError(ValueError):
    def __init__(
        self,
        stage: str,
        field: str,
        reason: str,
        *,
        claimed_skill: str | None = None,
        referenced_evidence_id: str | None = None,
        candidate_skill_id: str | None = None,
        job_evidence_id: str | None = None,
        requirement_id: str | None = None,
        classification: str | None = None,
        source_context: str | None = None,
    ):
        self.stage = stage
        self.field = field
        self.reason = reason
        self.claimed_skill = claimed_skill
        self.referenced_evidence_id = referenced_evidence_id
        self.candidate_skill_id = candidate_skill_id
        self.job_evidence_id = job_evidence_id
        self.requirement_id = requirement_id
        self.classification = classification
        self.source_context = source_context
        super().__init__(f"{stage}:{field}:{reason}")


class ReferencedFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal[
        "req", "pref", "exp", "duty", "match", "transfer", "missing", "eligibility", "unresolved"
    ]
    text: str = Field(min_length=1, max_length=200)
    job_evidence_id: str = Field(min_length=1, max_length=40)
    profile_evidence_id: str | None
    candidate_skill_id: str | None
    explanation: str | None = Field(default=None, max_length=400)
    # Accepted for local legacy fixtures; omitted from the Gemini schema and never trusted.
    matched: bool | None = None


class ReferencedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: list[ReferencedFinding]


class JobFinding(BaseModel):
    """Model output is limited to claims about the job posting."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["req", "pref", "exp", "duty", "eligibility", "unresolved"]
    text: str = Field(min_length=1, max_length=200)
    job_evidence_id: str = Field(min_length=1, max_length=40)


class JobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: list[JobFinding]


@dataclass(frozen=True)
class Segment:
    id: str
    field: str
    text: str

    def public(self) -> dict[str, str]:
        return {"id": self.id, "field": self.field, "text": self.text}


@dataclass(frozen=True)
class SkillSupport:
    evidence_id: str
    level: Literal["professional", "project", "listed"]
    project_id: str | None = None
    activity_evidence_ids: tuple[str, ...] = ()

    def public(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "level": self.level,
            "project_id": self.project_id,
            "activity_evidence_ids": list(self.activity_evidence_ids),
        }


@dataclass(frozen=True)
class CandidateSkill:
    id: str
    name: str
    supports: tuple[SkillSupport, ...]

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "supports": [x.public() for x in self.supports]}


@dataclass(frozen=True)
class ProjectEvidence:
    id: str
    index: int
    title: str
    technologies: tuple[str, ...]
    activities: str
    source_evidence_ids: tuple[str, ...]
    activity_evidence_ids: tuple[str, ...]

    def public(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "technologies": list(self.technologies),
            "activities": self.activities,
            "source_evidence_ids": list(self.source_evidence_ids),
            "activity_evidence_ids": list(self.activity_evidence_ids),
        }


def normalize_space(value: str) -> str:
    return " ".join(value.split()).casefold()


def phrase_in(value: str, phrase: str) -> bool:
    target = normalize_space(phrase)
    if not target:
        return False
    return bool(re.search(r"(?<!\w)" + re.escape(target) + r"(?!\w)", normalize_space(value)))


def canonical_skill(value: str) -> str:
    label = normalize_space(value)
    return ALIAS_TO_SKILL.get(label, label)


def atomic_skill(value: str) -> bool:
    label = normalize_space(value)
    return label in ALIAS_TO_SKILL or bool(re.fullmatch(r"[a-z][a-z0-9+#./-]{1,30}", label))


def _skill_mentions(text: str, name: str) -> list[re.Match]:
    aliases = SKILL_ALIASES.get(name, (name,))
    return [
        hit
        for alias in aliases
        for hit in re.finditer(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", normalize_space(text))
    ]


def _support_level(segment: Segment, name: str) -> str | None:
    normalized = normalize_space(segment.text)
    mentions = _skill_mentions(segment.text, name)
    listed = False
    header_end = -1
    if segment.field.startswith("projects[") and "|" in normalized:
        header_end = normalized.find(". ", normalized.find("|"))
    for hit in mentions:
        prefix = normalized[max(0, hit.start() - 45) : hit.start()]
        if re.search(
            r"\b(?:no|not|without|lack(?:ing)?|never|did not|have not)"
            r"(?:\s+[a-z]+){0,3}\s+$",
            prefix,
        ):
            continue
        if 0 <= header_end and hit.start() < header_end:
            listed = True
            continue
        if segment.field.startswith("experience["):
            level = "professional"
        elif segment.field.startswith("projects["):
            level = "project"
        else:
            level = "listed"
        window = normalized[max(0, hit.start() - 80) : min(len(normalized), hit.end() + 80)]
        if level != "listed" and not DEMONSTRATION.search(window):
            level = "listed"
        if level != "listed":
            return level
        listed = True
    return "listed" if listed else None


def build_project_evidence(profile: list[Segment]) -> list[ProjectEvidence]:
    """Group only text segments belonging to the same approved project entry."""
    groups: dict[int, list[tuple[int, Segment]]] = {}
    for item in profile:
        if match := PROJECT_FIELD.fullmatch(item.field):
            groups.setdefault(int(match.group(1)), []).append((int(match.group(2)), item))
    output = []
    for index, entries in sorted(groups.items()):
        segments = [item for _, item in sorted(entries)]
        text = " ".join(item.text for item in segments)
        if "|" in text:
            title, detail = text.split("|", 1)
            technology_line, separator, activities = detail.partition(". ")
            technologies = (
                tuple(part.strip() for part in technology_line.split(",") if part.strip())
                if separator
                else ()
            )
            activities = activities.strip() if separator else detail.strip()
        else:
            title = text.split(". ", 1)[0]
            technologies = ()
            activities = text
        digest = hashlib.sha256(f"{index}\0{normalize_space(text)}".encode()).hexdigest()[:12]
        activity_ids = tuple(item.id for item in segments if PROJECT_ACTION.search(item.text))
        output.append(
            ProjectEvidence(
                id=f"G-{digest}",
                index=index,
                title=title.strip(),
                technologies=technologies,
                activities=activities,
                source_evidence_ids=tuple(item.id for item in segments),
                activity_evidence_ids=activity_ids,
            )
        )
    return output


def _project_support(
    segment: Segment, name: str, groups: list[ProjectEvidence]
) -> ProjectEvidence | None:
    match = PROJECT_FIELD.fullmatch(segment.field)
    if match is None:
        return None
    group = next((item for item in groups if item.index == int(match.group(1))), None)
    if (
        group is None
        or not group.activity_evidence_ids
        or not PROJECT_ACTION.search(group.activities)
    ):
        return None
    direct_level = _support_level(segment, name)
    if direct_level == "project":
        return group
    if not any(canonical_skill(technology) == name for technology in group.technologies):
        return None
    relevant = PROJECT_RELEVANCE.get(name)
    if not (
        _skill_mentions(group.activities, name) or (relevant and relevant.search(group.activities))
    ):
        return None
    if direct_level is None:
        return None
    return group


def build_skill_catalog(profile: list[Segment]) -> list[CandidateSkill]:
    """Catalog only explicitly listed or named skills; never infer proficiency from a list."""
    projects = build_project_evidence(profile)
    names = {
        canonical_skill(item.text)
        for item in profile
        if item.field.startswith(("skills[", "technologies["))
    }
    names |= {
        name
        for name in SKILL_ALIASES
        for item in profile
        if item.field.startswith(("experience[", "projects[")) and _skill_mentions(item.text, name)
    }
    catalog = []
    for name in sorted(names):
        supports = []
        for item in profile:
            level = _support_level(item, name)
            if level is None:
                continue
            group = _project_support(item, name, projects)
            if group is not None:
                level = "project"
            supports.append(
                SkillSupport(
                    item.id,
                    level,
                    group.id if group else None,
                    group.activity_evidence_ids if group else (),
                )
            )
        if supports:
            digest = hashlib.sha256(name.encode()).hexdigest()[:12]
            catalog.append(CandidateSkill(f"S-{digest}", name, tuple(supports)))
    return catalog


def _chunks(value: str):
    for line in value.splitlines():
        remaining = line.strip()
        while remaining:
            if len(remaining) <= MAX_SEGMENT_CHARS:
                yield remaining
                break
            cut = remaining.rfind(". ", MAX_SEGMENT_CHARS // 2, MAX_SEGMENT_CHARS)
            cut = (
                cut + 1
                if cut >= 0
                else remaining.rfind(" ", MAX_SEGMENT_CHARS // 2, MAX_SEGMENT_CHARS)
            )
            if cut < 0:
                cut = MAX_SEGMENT_CHARS
            yield remaining[:cut].strip()
            remaining = remaining[cut:].strip()


def _make(prefix: str, field: str, text: str) -> Segment:
    digest = hashlib.sha256(f"{field}\0{normalize_space(text)}".encode()).hexdigest()[:12]
    return Segment(f"{prefix}-{digest}", field, text)


def build_segments(job: dict, profile: dict) -> tuple[list[Segment], list[Segment]]:
    jobs = []
    people = []

    def add(output, prefix, field, value):
        if value is None:
            return
        for index, chunk in enumerate(_chunks(str(value))):
            output.append(_make(prefix, f"{field}[{index}]", chunk))

    for field in ("title", "company", "workplace_type"):
        add(jobs, "J", field, job.get(field))
    add(jobs, "J", "description", job.get("description"))
    for index, value in enumerate(job.get("location", [])):
        add(jobs, "J", f"location[{index}]", value)
    for key, value in sorted(job.get("restrictions", {}).items()):
        if isinstance(value, list):
            for index, item in enumerate(value):
                add(jobs, "J", f"restrictions.{key}[{index}]", item)
        elif isinstance(value, (str, int, float)):
            add(jobs, "J", f"restrictions.{key}", value)

    for group in ("experience", "projects", "education", "certifications"):
        for index, entry in enumerate(profile.get(group, [])):
            add(people, "P", f"{group}[{index}].text", entry["text"])
            for date_index, date in enumerate(entry.get("dates", [])):
                add(people, "P", f"{group}[{index}].dates[{date_index}]", date)
    for group in ("skills", "technologies"):
        for index, value in enumerate(profile.get(group, [])):
            add(people, "P", f"{group}[{index}]", value)
    add(people, "P", "experience_years", profile.get("experience_years"))
    if len({item.id for item in jobs}) != len(jobs) or len({item.id for item in people}) != len(
        people
    ):
        raise ValueError("Evidence ID collision")
    return jobs, people


def minimum_years(value: str) -> float | None:
    match = YEAR_RANGE.search(value) or YEARS.search(value)
    return float(match.group(1)) if match else None


def _shared_technical_terms(job_text: str, profile_text: str) -> set[str]:
    left = {term.lower() for term in TECH_TERMS.findall(job_text)}
    right = {term.lower() for term in TECH_TERMS.findall(profile_text)}
    aliases = {"apis": "api", "tests": "testing", "models": "model", "coding": "code"}
    return {aliases.get(x, x) for x in left} & {aliases.get(x, x) for x in right}


def resolve(
    response: ReferencedResponse,
    job: list[Segment],
    profile: list[Segment],
    experience_years: float | None,
    catalog: list[CandidateSkill] | None = None,
) -> list[dict]:
    jobs = {item.id: item for item in job}
    people = {item.id: item for item in profile}
    projects = build_project_evidence(profile)
    skills = {
        item.id: item for item in (catalog if catalog is not None else build_skill_catalog(profile))
    }
    skills_by_name = {item.name: item for item in skills.values()}
    output = []
    for index, finding in enumerate(response.findings):
        field = f"findings[{index}]"
        source = jobs.get(finding.job_evidence_id)
        if source is None:
            raise EvidenceError(
                "evidence_reference",
                field + ".job_evidence_id",
                "unknown_id",
                claimed_skill=finding.text,
                candidate_skill_id=finding.candidate_skill_id,
                job_evidence_id=finding.job_evidence_id,
            )
        person = None
        if finding.profile_evidence_id is not None:
            person = people.get(finding.profile_evidence_id)
            if person is None:
                raise EvidenceError(
                    "evidence_reference",
                    field + ".profile_evidence_id",
                    "unknown_id",
                    claimed_skill=finding.text,
                    referenced_evidence_id=finding.profile_evidence_id,
                    candidate_skill_id=finding.candidate_skill_id,
                    job_evidence_id=source.id,
                )
        if finding.kind in {
            "req",
            "pref",
            "exp",
            "duty",
            "match",
            "transfer",
            "missing",
            "unresolved",
        }:
            if not source.field.startswith("description"):
                raise EvidenceError(
                    "claim_validation",
                    field + ".job_evidence_id",
                    "wrong_source_field",
                    claimed_skill=finding.text,
                    job_evidence_id=source.id,
                )
        if not phrase_in(source.text, finding.text):
            raise EvidenceError(
                "claim_validation",
                field + ".text",
                "claim_not_in_job_source",
                claimed_skill=finding.text,
                job_evidence_id=source.id,
            )
        state = "UNVERIFIED"
        support_level = None
        normalized = []
        skill_id = finding.candidate_skill_id
        if finding.matched is not None:
            normalized.append("ignored_legacy_matched_flag")
        if finding.kind in {"req", "pref"}:
            candidate_skill = (
                skills.get(skill_id)
                if skill_id
                else skills_by_name.get(canonical_skill(finding.text))
            )
            if skill_id is None and candidate_skill is not None and person is not None:
                skill_id = candidate_skill.id
                normalized.append("candidate_skill_id_from_catalog")
            details = {
                "claimed_skill": finding.text,
                "referenced_evidence_id": finding.profile_evidence_id,
                "candidate_skill_id": skill_id,
                "job_evidence_id": source.id,
            }
            if skill_id is not None and candidate_skill is None:
                raise EvidenceError(
                    "skill_validation", field + ".candidate_skill_id", "unknown_skill_id", **details
                )
            if candidate_skill is not None and candidate_skill.name != canonical_skill(
                finding.text
            ):
                raise EvidenceError(
                    "skill_validation",
                    field + ".candidate_skill_id",
                    "skill_label_mismatch",
                    **details,
                )
            if person is not None:
                if candidate_skill is None:
                    raise EvidenceError(
                        "skill_validation",
                        field + ".candidate_skill_id",
                        "unknown_skill_id",
                        **details,
                    )
                support = next(
                    (item for item in candidate_skill.supports if item.evidence_id == person.id),
                    None,
                )
                group = _project_support(person, candidate_skill.name, projects)
                verified_level = (
                    "project" if group is not None else _support_level(person, candidate_skill.name)
                )
                if support is None or (
                    verified_level != support.level
                    or support.project_id != (group.id if group else None)
                    or support.activity_evidence_ids
                    != (group.activity_evidence_ids if group else ())
                ):
                    raise EvidenceError(
                        "skill_validation",
                        field + ".profile_evidence_id",
                        "wrong_skill_evidence",
                        **details,
                    )
                support_level = support.level
                state = (
                    "MATCHED"
                    if support.level == "professional"
                    or (support.level == "project" and finding.kind == "pref")
                    else "PARTIAL"
                    if support.level == "project"
                    else "UNVERIFIED"
                )
            elif candidate_skill is None and atomic_skill(finding.text):
                state = "UNMATCHED"
        elif finding.candidate_skill_id is not None:
            raise EvidenceError(
                "skill_validation",
                field + ".candidate_skill_id",
                "unexpected_skill_id",
                claimed_skill=finding.text,
                referenced_evidence_id=finding.profile_evidence_id,
                candidate_skill_id=finding.candidate_skill_id,
            )
        years = minimum_years(finding.text)
        if finding.kind == "exp" and years is not None:
            if person is not None and not person.field.startswith("experience_years"):
                raise EvidenceError(
                    "claim_validation",
                    field + ".profile_evidence_id",
                    "approved_years_required",
                    claimed_skill=finding.text,
                    referenced_evidence_id=finding.profile_evidence_id,
                )
            state = (
                "MATCHED"
                if experience_years is not None and experience_years >= years
                else "UNMATCHED"
                if experience_years is not None
                else "UNVERIFIED"
            )
            normalized.append("experience_state_from_approved_years")
        elif finding.kind in {"duty", "match", "transfer"} and person is not None:
            if len(_shared_technical_terms(source.text, person.text)) < 2:
                raise EvidenceError(
                    "claim_validation",
                    field + ".profile_evidence_id",
                    "no_supported_alignment",
                    claimed_skill=finding.text,
                    referenced_evidence_id=finding.profile_evidence_id,
                )
            demonstrated = any(
                _skill_mentions(source.text, skill.name)
                and any(
                    ref.evidence_id == person.id and ref.level == level for ref in skill.supports
                )
                for skill in skills.values()
                for level in ("professional", "project")
            )
            if person.field.startswith("experience[") and demonstrated:
                support_level = "professional"
                state = "MATCHED" if finding.kind != "transfer" else "PARTIAL"
            elif person.field.startswith("projects[") and demonstrated:
                support_level = "project"
                state = "PARTIAL"
            else:
                support_level = "listed"
                state = "UNVERIFIED"
        if finding.kind == "missing":
            if any(
                item.name == canonical_skill(finding.text)
                and any(ref.level != "listed" for ref in item.supports)
                for item in skills.values()
            ) or any(phrase_in(item.text, finding.text) for item in profile):
                raise EvidenceError(
                    "claim_validation", field + ".text", "requirement_present_in_profile"
                )
            if years is not None and experience_years is not None and experience_years >= years:
                raise EvidenceError("claim_validation", field + ".text", "incorrect_experience_gap")
            if person is not None:
                raise EvidenceError(
                    "claim_validation",
                    field + ".profile_evidence_id",
                    "unexpected_profile_reference",
                    referenced_evidence_id=person.id,
                )
            state = "UNMATCHED"
        if finding.kind in {"eligibility", "unresolved"} and person is not None:
            raise EvidenceError(
                "claim_validation",
                field + ".profile_evidence_id",
                "unexpected_profile_reference",
                referenced_evidence_id=person.id,
            )
        output.append(
            {
                "kind": finding.kind,
                "text": finding.text,
                "matched": state in {"MATCHED", "PARTIAL"},
                "match_state": state,
                "job_evidence_id": source.id,
                "job_excerpt": source.text,
                "profile_evidence_id": person.id if person else None,
                "candidate_skill_id": skill_id,
                "skill_support_level": support_level,
                "profile_excerpt": person.text if person else None,
                "model_explanation": finding.explanation,
                "derived_fields": normalized,
                "interpretation_status": "model_inference"
                if finding.kind in {"match", "transfer", "missing"}
                else "evidence_checked",
            }
        )
    required_year_segments = {
        item.id
        for item in job
        if item.field.startswith("description") and minimum_years(item.text) is not None
    }
    covered = {item["job_evidence_id"] for item in output if item["kind"] == "exp"}
    if required_year_segments - covered:
        raise EvidenceError("coverage_validation", "findings", "missing_experience_requirement")
    if not any(item["kind"] in {"req", "pref", "exp", "duty"} for item in output):
        raise EvidenceError("coverage_validation", "findings", "no_scoring_evidence")
    transfer = {
        item["job_evidence_id"]: item
        for item in output
        if item["kind"] == "transfer" and item["match_state"] == "PARTIAL"
    }
    for item in output:
        if item["kind"] == "duty" and item["match_state"] == "UNVERIFIED":
            supporting = transfer.get(item["job_evidence_id"])
            if supporting is not None:
                item["match_state"] = "PARTIAL"
                item["matched"] = True
                item["profile_evidence_id"] = supporting["profile_evidence_id"]
                item["profile_excerpt"] = supporting["profile_excerpt"]
                item["skill_support_level"] = "transferable"
                item["derived_fields"].append("duty_partial_from_validated_transfer")
    return output


def resolve_job_requirements(
    response: JobResponse,
    job: list[Segment],
    profile: list[Segment],
    experience_years: float | None,
    catalog: list[CandidateSkill] | None = None,
) -> list[dict]:
    """Validate job claims, then select all candidate evidence locally."""
    jobs = {item.id: item for item in job}
    people = {item.id: item for item in profile}
    skills = catalog if catalog is not None else build_skill_catalog(profile)
    by_name = {item.name: item for item in skills}
    output = []
    for index, finding in enumerate(response.findings):
        field = f"findings[{index}]"
        source = jobs.get(finding.job_evidence_id)
        if source is None:
            raise EvidenceError("evidence_reference", field + ".job_evidence_id", "unknown_id")
        if not source.field.startswith("description"):
            raise EvidenceError(
                "claim_validation", field + ".job_evidence_id", "wrong_source_field"
            )
        if not phrase_in(source.text, finding.text):
            raise EvidenceError("claim_validation", field + ".text", "claim_not_in_job_source")
        state = "UNVERIFIED"
        support = None
        skill = None
        person = None
        derived = []
        if finding.kind in {"req", "pref"}:
            label = canonical_skill(finding.text)
            skill = by_name.get(label)
            if re.search(r"\b(?:at least one of|one of|either)\b", source.text, re.I):
                skill = None
                derived.append("alternative_requirement_unresolved")
            elif skill is not None:
                # A demonstrated professional use outranks a project; a list never does.
                support = next((x for x in skill.supports if x.level == "professional"), None)
                if support is None:
                    support = next((x for x in skill.supports if x.level == "project"), None)
                if support is None:
                    support = next((x for x in skill.supports if x.level == "listed"), None)
                if support.level == "professional":
                    state = "MATCHED"
                elif support.level == "project":
                    state = "PARTIAL" if finding.kind == "req" else "MATCHED"
                derived.append("candidate_skill_from_verified_catalog")
            elif atomic_skill(finding.text):
                state = "UNMATCHED"
            else:
                derived.append("ambiguous_skill_requirement")
        elif finding.kind == "exp":
            minimum = minimum_years(finding.text)
            if minimum is not None and experience_years is not None:
                state = "MATCHED" if experience_years >= minimum else "UNMATCHED"
                derived.append("experience_state_from_approved_years")
        elif finding.kind == "duty":
            # Shared words alone are insufficient: require a catalogued demonstrated skill.
            for candidate in skills:
                if not _skill_mentions(source.text, candidate.name):
                    continue
                for level in ("professional", "project"):
                    found = next(
                        (
                            x
                            for x in candidate.supports
                            if x.level == level
                            and len(
                                _shared_technical_terms(source.text, people[x.evidence_id].text)
                            )
                            >= 2
                        ),
                        None,
                    )
                    if found:
                        support, skill = found, candidate
                        state = "MATCHED" if level == "professional" else "PARTIAL"
                        break
                if support:
                    break
            if support is None:
                specific = {
                    "python",
                    "ruby",
                    "rails",
                    "sql",
                    "api",
                    "react",
                    "typescript",
                    "javascript",
                    "docker",
                    "linux",
                    "git",
                    "aws",
                    "kubernetes",
                    "fastapi",
                    "django",
                    "flask",
                }
                for person_candidate in profile:
                    shared = _shared_technical_terms(source.text, person_candidate.text)
                    if (
                        person_candidate.field.startswith(("experience[", "projects["))
                        and len(shared) >= 2
                        and shared & specific
                        and DEMONSTRATION.search(person_candidate.text)
                    ):
                        person = person_candidate
                        state = "PARTIAL"
                        derived.append("transferable_alignment_from_shared_technical_terms")
                        break
        if support:
            person = people[support.evidence_id]
        output.append(
            {
                "kind": finding.kind,
                "text": finding.text,
                "matched": state in {"MATCHED", "PARTIAL"},
                "match_state": state,
                "job_evidence_id": source.id,
                "job_excerpt": source.text,
                "profile_evidence_id": person.id if person else None,
                "candidate_skill_id": skill.id if skill else None,
                "skill_support_level": support.level
                if support
                else "transferable"
                if person
                else None,
                "profile_excerpt": person.text if person else None,
                "model_explanation": None,
                "derived_fields": derived,
                "interpretation_status": "evidence_checked",
            }
        )
    required_year_segments = {
        item.id
        for item in job
        if item.field.startswith("description") and minimum_years(item.text) is not None
    }
    covered = {item["job_evidence_id"] for item in output if item["kind"] == "exp"}
    if required_year_segments - covered:
        raise EvidenceError("coverage_validation", "findings", "missing_experience_requirement")
    if not any(item["kind"] in {"req", "pref"} for item in output):
        raise EvidenceError("coverage_validation", "findings", "missing_skill_requirements")
    if not any(item["kind"] == "duty" for item in output):
        raise EvidenceError("coverage_validation", "findings", "missing_responsibilities")
    in_requirements = False
    explicit_segments = set()
    for segment in job:
        if not segment.field.startswith("description"):
            continue
        heading = normalize_space(segment.text).rstrip(":")
        if heading in {"requirements", "required qualifications", "minimum qualifications"}:
            in_requirements = True
            continue
        if heading in {"nice to have", "preferred qualifications", "benefits", "terms"}:
            in_requirements = False
        elif in_requirements:
            explicit_segments.add(segment.id)
    covered_segments = {item["job_evidence_id"] for item in output}
    if explicit_segments - covered_segments:
        raise EvidenceError(
            "coverage_validation", "findings", "missing_explicit_requirement_segment"
        )
    return output
