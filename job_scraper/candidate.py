"""Local, reviewable professional profile extraction from an explicitly supplied PDF."""

import hashlib
import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pypdf import PdfReader
from pypdf.errors import PdfReadError

PROFILE_SCHEMA = "candidate-v1"
SECTION = re.compile(
    r"^(?:professional |work )?(experience|employment|skills|technical skills|"
    r"projects|education|certifications)\s*:?$",
    re.I,
)
EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)")
URL = re.compile(r"\b(?:https?://|www\.)\S+", re.I)
ADDRESS = re.compile(
    r"\b(?:\d{1,6}\s+[^,\n]{2,70}\s+(?:street|st\.?|road|rd\.?|avenue|ave\.?|"
    r"lane|ln\.?|drive|dr\.?|boulevard|blvd\.?|apartment|apt\.?|flat))\b|"
    r"\bP\.?O\.?\s*Box\s*\d+\b",
    re.I,
)
CONTACT_LINE = re.compile(
    r"\b(?:e-?mail|phone|mobile|whatsapp|address|linkedin|github|portfolio|contact)\b",
    re.I,
)
DIRECT_CONTACT_LINE = re.compile(
    r"^(?:address|home address|phone|mobile|whatsapp|e-?mail|contact|linkedin|github|portfolio)\s*:",
    re.I,
)
DATE = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{4}\b|\b(?:19|20)\d{2}\b|\bPresent\b",
    re.I,
)
TECHNOLOGIES = (
    "Python",
    "Django",
    "FastAPI",
    "Flask",
    "Rails",
    "Ruby",
    "JavaScript",
    "TypeScript",
    "React",
    "Node.js",
    "PostgreSQL",
    "SQLite",
    "MySQL",
    "AWS",
    "Azure",
    "GCP",
    "Docker",
    "Kubernetes",
    "Terraform",
    "PyTorch",
    "TensorFlow",
    "LangChain",
    "SQL",
    "GraphQL",
    "Redis",
    "Git",
)


def sanitize_text(value: str) -> str:
    """Redact direct contact data; omit address/contact lines entirely."""
    lines = []
    for raw in value.splitlines():
        if ADDRESS.search(raw) or DIRECT_CONTACT_LINE.search(raw.strip()):
            continue
        has_phone = any(sum(c.isdigit() for c in m.group()) >= 10 for m in PHONE.finditer(raw))
        if CONTACT_LINE.search(raw) and (EMAIL.search(raw) or has_phone or URL.search(raw)):
            continue
        line = URL.sub("[redacted URL]", EMAIL.sub("[redacted email]", raw))
        line = PHONE.sub(
            lambda match: (
                "[redacted phone]"
                if sum(c.isdigit() for c in match.group()) >= 10
                else match.group()
            ),
            line,
        )
        if line.strip():
            lines.append(line.strip())
    return "\n".join(lines)


class ProfileEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=3000)
    dates: list[str] = Field(default_factory=list)


class CandidateProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = PROFILE_SCHEMA
    experience: list[ProfileEntry] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    projects: list[ProfileEntry] = Field(default_factory=list)
    education: list[ProfileEntry] = Field(default_factory=list)
    certifications: list[ProfileEntry] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    experience_years: float | None = Field(default=None, ge=0, le=80)

    @field_validator("schema_version")
    @classmethod
    def current_schema(cls, value):
        if value != PROFILE_SCHEMA:
            raise ValueError("Unsupported profile schema")
        return value


def assert_sanitized(profile: CandidateProfile) -> None:
    data = profile.model_dump_json()
    has_phone = any(sum(c.isdigit() for c in m.group()) >= 10 for m in PHONE.finditer(data))
    if EMAIL.search(data) or has_phone or URL.search(data) or ADDRESS.search(data):
        raise ValueError("Profile still contains contact or address information")


def profile_json(profile: CandidateProfile) -> str:
    assert_sanitized(profile)
    result = json.dumps(profile.model_dump(), sort_keys=True, ensure_ascii=False)
    if len(result) > 20_000:
        raise ValueError("Professional profile exceeds 20,000 characters; shorten it locally")
    return result


def profile_hash(profile: CandidateProfile) -> str:
    return hashlib.sha256(profile_json(profile).encode()).hexdigest()


def extract_pdf(path: Path) -> CandidateProfile:
    path = path.expanduser().resolve(strict=True)
    if path.suffix.lower() != ".pdf" or path.stat().st_size > 10_000_000:
        raise ValueError("Provide a PDF smaller than 10 MB")
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted or len(reader.pages) > 20:
            raise ValueError("Encrypted or over-20-page PDFs are not supported")
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except PdfReadError:
        raise ValueError("PDF could not be read") from None
    if not text.strip():
        raise ValueError("No extractable text; scanned PDFs require local OCR")
    sections: dict[str, list[str]] = {
        k: [] for k in ("experience", "skills", "projects", "education", "certifications")
    }
    active = None
    for raw in text.splitlines():
        heading = SECTION.fullmatch(raw.strip())
        if heading:
            name = heading.group(1).lower()
            active = (
                "experience"
                if name in {"experience", "employment"}
                else "skills"
                if name in {"skills", "technical skills"}
                else name
            )
            continue
        if active:
            clean = sanitize_text(raw)
            if clean:
                sections[active].append(clean)
    entries = {
        name: [ProfileEntry(text=line, dates=DATE.findall(line)) for line in sections[name]]
        for name in ("experience", "projects", "education", "certifications")
    }
    skill_text = "\n".join(sections["skills"])
    skills = [s.strip(" •-\t") for s in re.split(r"[,;|\n•]", skill_text) if s.strip(" •-\t")]
    professional = "\n".join(x for values in sections.values() for x in values)
    technologies = [
        tech
        for tech in TECHNOLOGIES
        if re.search(r"(?<!\w)" + re.escape(tech) + r"(?!\w)", professional, re.I)
    ]
    profile = CandidateProfile(
        **entries,
        skills=list(dict.fromkeys(skills)),
        technologies=technologies,
    )
    assert_sanitized(profile)
    return profile
