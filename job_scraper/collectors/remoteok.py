from .base import Collector, strings


class RemoteOkCollector(Collector):
    """Remote OK public JSON feed.

    Terms: link back to Remote OK and name it as the source. The first array element is a legal
    notice, not a job, and is skipped.
    """

    endpoint = "https://remoteok.com/api"

    def collect(self):
        payload = self.get(self.endpoint)
        rows = self.records(payload, key=None)
        yield from self.normalize_records(
            [row for row in rows if "id" in row and "position" in row]
        )

    def normalize(self, raw):
        return self.job(
            raw,
            source_id=raw["id"],
            company=raw["company"],
            title=raw["position"],
            description_html=raw.get("description"),
            location=strings(raw.get("location")),
            posted_at=raw.get("date"),
            source_url=raw["url"],
            application_url=None,
            attribution="Remote OK",
            attribution_url="https://remoteok.com",
        )
