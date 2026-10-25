import logging

from ..http import CollectionError
from ..normalization import greenhouse_salary
from .base import Collector, employment


class GreenhouseCollector(Collector):
    def collect(self):
        url = f"https://boards-api.greenhouse.io/v1/boards/{self.config.board}/jobs"
        payload = self.get(url, params={"content": "true"})
        rows = self.records(payload)
        fetch_details = self.config.fetch_details
        for raw in rows:
            original = raw
            if fetch_details and isinstance(raw, dict) and isinstance(raw.get("id"), int):
                try:
                    detail = self.get(f"{url}/{raw['id']}", params={"pay_transparency": "true"})
                    if not isinstance(detail, dict) or detail.get("id") != raw["id"]:
                        raise CollectionError("Greenhouse detail response has unexpected job ID")
                    raw = {**raw, **detail}
                except CollectionError as exc:
                    # Stop detail enrichment after its retry budget is exhausted;
                    # do not hammer a denied or rate-limited endpoint for every job.
                    fetch_details = False
                    self.stats.errors += 1
                    logging.getLogger(__name__).warning(
                        "Greenhouse detail failed board=%s; using list records for remaining jobs: %s",
                        self.config.board,
                        exc,
                    )
            metadata = {k: v for k, v in payload.items() if k != "jobs"}
            metadata["list_record"] = original
            yield from self.normalize_records([raw], metadata)

    def normalize(self, raw):
        metadata = raw.get("metadata") or []
        job_type = next(
            (
                m.get("value")
                for m in metadata
                if m.get("name", "").lower() in {"employment type", "employment_type"}
            ),
            None,
        )
        ranges = raw.get("pay_input_ranges")
        return self.job(
            raw,
            source_id=raw["id"],
            title=raw["title"],
            description_html=raw.get("content"),
            location=[raw["location"]["name"]] if (raw.get("location") or {}).get("name") else [],
            employment_type=employment(job_type),
            salary=greenhouse_salary(ranges),
            posted_at=raw.get("first_published"),
            updated_at=raw.get("updated_at"),
            source_url=raw["absolute_url"],
            application_url=raw["absolute_url"],
            attribution_url="https://www.greenhouse.com",
        )
