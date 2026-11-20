from .base import Collector, employment, strings


class JobicyCollector(Collector):
    """Jobicy public remote-jobs API (https://jobi.cy/apidocs).

    Terms: credit Jobicy with a link and send applicants to the original job URL; this tool keeps
    the Jobicy URL as the source and application link and records the attribution.
    """

    endpoint = "https://jobicy.com/api/v2/remote-jobs"

    def collect(self):
        payload = self.get(self.endpoint, params={"count": 100})
        rows = self.records(payload, key="jobs")
        yield from self.normalize_records(rows, {})

    def normalize(self, raw):
        # jobGeo is a free-text list of countries or regions ("Poland,  Portugal", "Anywhere").
        geo = [part.strip() for value in strings(raw.get("jobGeo")) for part in value.split(",")]
        return self.job(
            raw,
            source_id=raw["id"],
            company=raw["companyName"],
            title=raw["jobTitle"],
            description_html=raw.get("jobDescription") or raw.get("jobExcerpt"),
            location=[x for x in geo if x],
            restrictions={"jobLevel": raw["jobLevel"]} if raw.get("jobLevel") else {},
            employment_type=employment(raw.get("jobType")),
            posted_at=raw.get("pubDate"),
            source_url=raw["url"],
            application_url=None,
            attribution="Jobicy",
            attribution_url="https://jobicy.com",
        )
