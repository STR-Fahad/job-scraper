import re

from .base import Collector, strings


class WorkingNomadsCollector(Collector):
    """Working Nomads public JSON feed of remote jobs."""

    endpoint = "https://www.workingnomads.com/api/exposed_jobs/"

    def collect(self):
        payload = self.get(self.endpoint)
        rows = self.records(payload, key=None)
        yield from self.normalize_records(rows)

    def normalize(self, raw):
        match = re.search(r"/(\d+)/?$", raw["url"])
        return self.job(
            raw,
            # The numeric id in the job URL is stable; fall back to the whole URL.
            source_id=match[1] if match else raw["url"],
            company=raw["company_name"],
            title=raw["title"],
            description_html=raw.get("description"),
            location=strings(raw.get("location")),
            posted_at=raw.get("pub_date"),
            source_url=raw["url"],
            application_url=None,
            attribution="Working Nomads",
            attribution_url="https://www.workingnomads.com",
        )
