from ..normalization import ashby_salary
from .base import Collector, employment, strings


class AshbyCollector(Collector):
    def collect(self):
        url = f"https://api.ashbyhq.com/posting-api/job-board/{self.config.board}"
        payload = self.get(url, params={"includeCompensation": "true"})
        rows = self.records(payload)
        yield from self.normalize_records(rows, {k: v for k, v in payload.items() if k != "jobs"})

    def normalize(self, raw):
        compensation = raw.get("compensation")
        return self.job(
            raw,
            source_id=raw.get("id") or raw["jobUrl"],
            title=raw["title"],
            description_html=raw.get("descriptionHtml"),
            description=raw.get("descriptionPlain") or None,
            location=strings(raw.get("location")) + strings(raw.get("secondaryLocations")),
            employment_type=employment(raw.get("employmentType")),
            salary=ashby_salary(compensation),
            is_listed=raw.get("isListed", True),
            posted_at=raw.get("publishedAt"),
            source_url=raw["jobUrl"],
            application_url=raw.get("applyUrl"),
            attribution_url="https://www.ashbyhq.com",
        )
