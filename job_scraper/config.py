"""TOML source registry. Paths are resolved relative to the configuration file."""

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import Source


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Source
    enabled: bool = True
    board: str = Field(default="", pattern=r"^[A-Za-z0-9_.-]*$")
    company: str | None = None
    region: str = Field(default="global", pattern=r"^(global|eu)$")
    max_pages: int = Field(default=100, ge=1, le=10000)
    page_size: int = Field(default=20, ge=1, le=100)
    interval_seconds: float = Field(default=2, ge=0.1)
    cooldown_seconds: float = Field(default=0, ge=0)
    fetch_details: bool = True
    empty_confirmation_seconds: float = Field(default=3600, ge=0)

    @model_validator(mode="after")
    def validate_source(self):
        if self.board in {".", ".."}:
            raise ValueError("board must be an identifier, not a path segment")
        if self.source in {"greenhouse", "ashby", "lever"}:
            if not self.board or not self.company or not self.company.strip():
                raise ValueError("ATS sources require board and company")
        elif self.board:
            raise ValueError("aggregate sources do not accept board identifiers")
        if self.source != "lever" and self.region != "global":
            raise ValueError("region is supported only for Lever")
        if self.source == "himalayas":
            self.page_size = min(self.page_size, 20)
            self.cooldown_seconds = max(self.cooldown_seconds, 86400)
        if self.source == "remotive":
            self.interval_seconds = max(self.interval_seconds, 31)
            self.cooldown_seconds = max(self.cooldown_seconds, 21600)
        return self

    @property
    def scope(self) -> str:
        return f"{self.region}:{self.board}" if self.source == "lever" else self.board


class MatchingWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")
    required_skills: int = Field(default=40, ge=0, le=100)
    experience: int = Field(default=25, ge=0, le=100)
    role_alignment: int = Field(default=20, ge=0, le=100)
    preferred_projects: int = Field(default=15, ge=0, le=100)

    @model_validator(mode="after")
    def total_one_hundred(self):
        if sum(self.model_dump().values()) != 100:
            raise ValueError("matching weights must total 100")
        return self


class MatchingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_requests_per_run: int = Field(default=3, ge=1, le=5)
    retries: int = Field(default=1, ge=0, le=3)
    timeout_seconds: int = Field(default=120, ge=10, le=300)
    weights: MatchingWeights = Field(default_factory=MatchingWeights)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    database: Path = Path("data/jobs.sqlite3")
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    retries: int = Field(default=3, ge=0, le=8)
    max_retry_delay: float = Field(default=120, ge=1, le=3600)
    sources: list[SourceConfig]
    matching: MatchingConfig = Field(default_factory=MatchingConfig)

    @model_validator(mode="after")
    def unique_registry(self):
        keys = [(s.source, s.scope) for s in self.sources]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate source/board registry entries")
        return self


def load_settings(path: Path) -> Settings:
    with path.open("rb") as stream:
        settings = Settings.model_validate(tomllib.load(stream))
    if not settings.database.is_absolute():
        settings.database = path.resolve().parent / settings.database
    return settings
