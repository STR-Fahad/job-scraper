import os

from ..http import CollectionError
from ..models import Salary
from .base import Collector, employment, strings


class HimalayasCollector(Collector):
    endpoint = "https://himalayas.app/jobs/api"

    def collect(self):
        params = {"limit": self.config.page_size}
        seen = set()
        token = os.environ.get("HIMALAYAS_API_KEY")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        for _ in range(self.config.max_pages):
            payload = self.get(self.endpoint, params=params, headers=headers)
            rows = self.records(payload)
            yield from self.normalize_records(
                rows, {k: v for k, v in payload.items() if k != "jobs"}
            )
            cursor = payload.get("nextCursor")
            if not cursor:
                return
            if not isinstance(cursor, str) or cursor in seen:
                raise CollectionError("Himalayas repeated or invalid cursor")
            seen.add(cursor)
            params["cursor"] = cursor
        self.stats.truncated = True

    def normalize(self, raw):
        guid = raw["guid"]
        # Current feed uses URL guids. Older opaque guids have no documented
        # conversion to a detail URL: retain the supplied applicationLink instead.
        source_url = guid if guid.startswith(("https://", "http://")) else raw["applicationLink"]
        return self.job(
            raw,
            source_id=guid,
            company=raw["companyName"],
            title=raw["title"],
            description_html=raw.get("description"),
            location=strings(raw.get("locationRestrictions")),
            restrictions={
                k: raw[k] for k in ("locationRestrictions", "timezoneRestrictions") if k in raw
            },
            employment_type=employment(raw.get("employmentType")),
            salary=Salary(
                minimum=raw.get("minSalary"),
                maximum=raw.get("maxSalary"),
                currency=raw.get("currency"),
                period=raw.get("salaryPeriod", "annual"),
                compensation_type="base_salary",
            )
            if raw.get("minSalary") is not None or raw.get("maxSalary") is not None
            else None,
            updated_at=raw.get("pubDate"),
            expires_at=raw.get("expiryDate"),
            source_url=source_url,
            application_url=raw.get("applicationLink"),
            attribution_url="https://himalayas.app",
        )
