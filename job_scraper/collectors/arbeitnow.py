from urllib.parse import urljoin, urlsplit

from ..http import CollectionError
from .base import Collector, employment, strings


class ArbeitnowCollector(Collector):
    endpoint = "https://www.arbeitnow.com/api/job-board-api"

    def collect(self):
        url = self.endpoint
        seen = set()
        for _ in range(self.config.max_pages):
            if url in seen:
                raise CollectionError("Arbeitnow repeated next link")
            seen.add(url)
            payload = self.get(url)
            rows = self.records(payload, key="data")
            yield from self.normalize_records(
                rows, {k: v for k, v in payload.items() if k != "data"}
            )
            next_url = payload.get("links", {}).get("next")
            if not next_url:
                return
            url = urljoin(self.endpoint, next_url)
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.netloc != "www.arbeitnow.com"
                or parsed.path != "/api/job-board-api"
            ):
                raise CollectionError("Arbeitnow next link outside documented API")
        self.stats.truncated = True

    def normalize(self, raw):
        return self.job(
            raw,
            source_id=raw["slug"],
            company=raw["company_name"],
            title=raw["title"],
            description_html=raw.get("description"),
            location=strings(raw.get("location")),
            employment_type=employment(raw.get("job_types")),
            posted_at=raw.get("created_at"),
            source_url=raw["url"],
            application_url=None,
            attribution_url="https://www.arbeitnow.com",
        )
