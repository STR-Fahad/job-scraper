import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Iterator
from html import unescape
from typing import Any

from bs4 import BeautifulSoup
from pydantic import ValidationError

from ..config import SourceConfig
from ..http import CollectionError, HttpClient
from ..models import Job, Statistics
from ..normalization import workplace

logger = logging.getLogger(__name__)


def text(html: str | None) -> str:
    value = html or ""
    # Greenhouse and Arbeitnow can supply entity-encoded HTML.
    for _ in range(2):
        if re.search(r"&(?:amp;)?lt;/?[A-Za-z]", value):
            value = unescape(value)
    soup = BeautifulSoup(value, "html.parser")
    for element in soup(["script", "style"]):
        element.decompose()
    return soup.get_text("\n", strip=True)


def strings(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        raise ValueError("expected a string or list")
    result = []
    for item in value:
        if isinstance(item, dict):
            item = item.get("name") or item.get("location") or item.get("alpha2")
        if item is None:
            continue
        if not isinstance(item, str):
            raise ValueError("expected string list entries")
        if item.strip():
            result.append(item.strip())
    return result


def employment(value) -> list[str]:
    aliases = {
        "fulltime": "full_time",
        "parttime": "part_time",
        "contractor": "contract",
        "contract": "contract",
        "intern": "internship",
        "internship": "internship",
        "temporary": "temporary",
        "freelance": "freelance",
        "volunteer": "volunteer",
    }
    return [aliases.get(re.sub(r"[\s_-]", "", x.lower()), x.lower()) for x in strings(value)]


class Collector(ABC):
    def __init__(self, config: SourceConfig, http: HttpClient, stats: Statistics):
        self.config, self.http, self.stats = config, http, stats

    def get(self, url, **kwargs):
        return self.http.get(url, interval=self.config.interval_seconds, **kwargs)

    def records(self, payload, key="jobs") -> list:
        records = (
            payload if key is None else payload.get(key) if isinstance(payload, dict) else None
        )
        if not isinstance(records, list):
            raise CollectionError(f"{self.config.source}: expected {key or 'root'} array")
        self.stats.pages += 1
        return records

    def normalize_records(self, rows, metadata=None) -> Iterator[Job]:
        for raw in rows:
            self.stats.fetched += 1
            try:
                if not isinstance(raw, dict):
                    raise ValueError("record must be an object")
                job = self.normalize(raw)
                job.response_metadata = metadata or {}
                yield job
            except (ValidationError, ValueError, TypeError, KeyError, AttributeError) as exc:
                self.stats.invalid += 1
                # Do not log raw payloads, signed URLs, or validation input values.
                logger.warning(
                    "Invalid record source=%s board=%s record=%s type=%s",
                    self.config.source,
                    self.config.scope,
                    self.stats.fetched,
                    type(exc).__name__,
                )

    def job(
        self,
        raw: dict,
        *,
        source_id,
        company=None,
        description_html=None,
        description=None,
        **kwargs,
    ) -> Job:
        if source_id is None or isinstance(source_id, (dict, list, bool)):
            raise ValueError("missing or invalid source identifier")
        return Job(
            source=self.config.source,
            board=self.config.scope,
            source_id=str(source_id),
            company=company or self.config.company,
            description_html=description_html,
            description=description if description is not None else text(description_html),
            raw=raw,
            workplace_type=workplace(self.config.source, raw),
            attribution=self.config.source.capitalize(),
            **kwargs,
        )

    @abstractmethod
    def collect(self) -> Iterator[Job]: ...

    @abstractmethod
    def normalize(self, raw: dict[str, Any]) -> Job: ...
