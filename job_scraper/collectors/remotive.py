from ..models import Salary
from .base import Collector, employment, strings


class RemotiveCollector(Collector):
    def collect(self):
        payload = self.get("https://remotive.com/api/remote-jobs")
        rows = self.records(payload)
        yield from self.normalize_records(rows, {k: v for k, v in payload.items() if k != "jobs"})

    def normalize(self, raw):
        return self.job(
            raw,
            source_id=raw["id"],
            company=raw["company_name"],
            title=raw["title"],
            description_html=raw.get("description"),
            location=strings(raw.get("candidate_required_location")),
            restrictions={"candidate_required_location": raw["candidate_required_location"]}
            if "candidate_required_location" in raw
            else {},
            employment_type=employment(raw.get("job_type")),
            salary=Salary(text=raw["salary"]) if raw.get("salary") else None,
            posted_at=raw.get("publication_date"),
            source_url=raw["url"],
            # Remotive exposes a listing URL, not a distinct direct apply URL.
            application_url=None,
            attribution_url="https://remotive.com",
        )
