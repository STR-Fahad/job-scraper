"""Validated, source-independent records; missing facts stay unknown."""

import re
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

Source = Literal["himalayas", "greenhouse", "ashby", "lever", "remotive", "arbeitnow"]


def canonical_url(value: str) -> str:
    """Conservative identity: remove only known tracking keys, preserve job query IDs.

    Do not fetch redirects, drop arbitrary queries, lowercase paths, or equate
    HTTP/HTTPS. Never use this identity URL to replace a source's apply link.
    """
    parsed = urlsplit(str(HttpUrl(value)))
    query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid"}
    ]
    # Hash routes can identify jobs. Only ordinary page anchors are discarded.
    fragment = parsed.fragment if parsed.fragment.startswith(("/", "!")) else ""
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(sorted(query)), fragment)
    )


class Compensation(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    minimum: float | None = None
    maximum: float | None = None
    currency: str | None = None
    period: str | None = None
    compensation_type: str = "unknown"


class Salary(Compensation):
    text: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    components: list[Compensation] = Field(default_factory=list)


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Source
    board: str = ""
    source_id: str = Field(min_length=1)
    company: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    description_html: str | None = None
    location: list[str] = Field(default_factory=list)
    restrictions: dict[str, Any] = Field(default_factory=dict)
    employment_type: list[str] = Field(default_factory=list)
    salary: Salary | None = None
    posted_at: datetime | None = None
    updated_at: datetime | None = None
    expires_at: datetime | None = None
    workplace_type: Literal["remote", "hybrid", "onsite", "unknown"] = "unknown"
    is_listed: bool = True
    source_url: HttpUrl
    application_url: HttpUrl | None = None
    attribution: str
    attribution_url: HttpUrl
    raw: dict[str, Any] = Field(default_factory=dict)
    response_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def legacy_remote(cls, data):
        if isinstance(data, dict) and "remote" in data:
            data = dict(data)
            data.pop("remote")
            from .normalization import workplace

            data.setdefault("workplace_type", workplace(data.get("source"), data.get("raw", {})))
        return data

    @property
    def remote(self) -> bool | None:
        """Compatibility accessor; consumers should use workplace_type."""
        return {"remote": True, "onsite": False}.get(self.workplace_type)

    @field_validator("source_id", "company", "title")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator("posted_at", "updated_at", "expires_at")
    @classmethod
    def utc_dates(cls, value: datetime | None) -> datetime | None:
        # APIs such as Remotive emit timestamps without a zone. Document UTC assumption.
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @property
    def identity_urls(self) -> set[str]:
        urls = {canonical_url(str(self.source_url))}
        # Link cross-source records only when an application URL encodes a known
        # ATS job ID. Never equate jobs sharing a generic careers/application page.
        for value in (self.source_url, self.application_url):
            if value is None:
                continue
            parsed = urlsplit(str(value))
            if parsed.hostname in {"jobs.lever.co", "jobs.eu.lever.co", "jobs.ashbyhq.com"}:
                match = re.fullmatch(
                    r"(/[^/]+/[0-9a-fA-F-]{32,36})(?:/(?:apply|application))?/?", parsed.path
                )
                if match:
                    urls.add(urlunsplit((parsed.scheme, parsed.netloc, match[1], "", "")))
            elif parsed.hostname in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
                match = re.fullmatch(r"(/[^/]+/jobs/[0-9]+)/?", parsed.path)
                if match:
                    urls.add(urlunsplit((parsed.scheme, "boards.greenhouse.io", match[1], "", "")))
        return urls


class Statistics(BaseModel):
    source: str
    board: str = ""
    pages: int = 0
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    duplicates: int = 0
    merged: int = 0
    invalid: int = 0
    errors: int = 0
    skipped: bool = False
    truncated: bool = False
    closed: int = 0
    expired: int = 0
    hidden: int = 0
    exhaustive: bool = False
    empty_confirmation_pending: bool = False

    @property
    def rejected(self) -> int:
        return self.invalid
