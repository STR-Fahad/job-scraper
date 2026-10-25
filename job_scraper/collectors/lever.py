from html import escape

from ..http import CollectionError
from ..models import Salary
from ..normalization import period
from .base import Collector, employment, strings, text


class LeverCollector(Collector):
    def collect(self):
        host = "api.eu.lever.co" if self.config.region == "eu" else "api.lever.co"
        url = f"https://{host}/v0/postings/{self.config.board}"
        seen_pages = set()
        skip = 0
        for _ in range(self.config.max_pages):
            payload = self.get(
                url, params={"mode": "json", "skip": skip, "limit": self.config.page_size}
            )
            rows = self.records(payload, key=None)
            fingerprint = repr(rows)
            if rows and fingerprint in seen_pages:
                raise CollectionError("Lever repeated page; stopping pagination")
            seen_pages.add(fingerprint)
            yield from self.normalize_records(rows)
            if len(rows) < self.config.page_size:
                return
            skip += len(rows)
        self.stats.truncated = True

    def normalize(self, raw):
        categories = raw.get("categories") or {}
        # Description alone excludes responsibilities, qualifications, and closing.
        parts = [raw.get("description") or escape(raw.get("descriptionPlain") or "")]
        for section in raw.get("lists") or []:
            parts += [f"<h2>{escape(section.get('text') or '')}</h2>", section.get("content") or ""]
        parts.append(raw.get("additional") or escape(raw.get("additionalPlain") or ""))
        salary_description = raw.get("salaryDescription") or escape(
            raw.get("salaryDescriptionPlain") or ""
        )
        if salary_description:
            parts.append(salary_description)
        salary = raw.get("salaryRange")
        return self.job(
            raw,
            source_id=raw["id"],
            title=raw["text"],
            description_html="\n".join(parts),
            location=strings(categories.get("allLocations") or categories.get("location")),
            employment_type=employment(categories.get("commitment")),
            salary=Salary(
                minimum=salary.get("min"),
                maximum=salary.get("max"),
                currency=salary.get("currency"),
                period=period(salary.get("interval")),
                compensation_type="base_salary",
                text=text(salary_description) or None,
                details=salary,
            )
            if salary
            else Salary(text=text(salary_description))
            if salary_description
            else None,
            source_url=raw["hostedUrl"],
            application_url=raw.get("applyUrl"),
            attribution_url="https://www.lever.co",
        )
