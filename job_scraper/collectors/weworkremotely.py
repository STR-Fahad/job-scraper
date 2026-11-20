from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from ..http import CollectionError
from .base import Collector, employment, strings

# Programming and operations category feeds (each stays under the HTTP client's size limit).
FEEDS = (
    "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-front-end-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
)


def _child(item, name) -> str:
    element = item.find(name)
    return (element.text or "").strip() if element is not None else ""


class WeWorkRemotelyCollector(Collector):
    """We Work Remotely public RSS category feeds (full-stack, back-end, front-end, devops)."""

    def collect(self):
        for url in FEEDS:
            text = self.get(url, as_text=True)
            # Untrusted XML: refuse DTDs and entity declarations outright.
            if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
                raise CollectionError("We Work Remotely feed contained a DTD; refused")
            try:
                root = ElementTree.fromstring(text)
            except ElementTree.ParseError as exc:
                raise CollectionError("Invalid XML from weworkremotely.com") from exc
            items = [self._record(item) for item in root.iter("item")]
            self.stats.pages += 1
            yield from self.normalize_records(items, {"feed": url})

    @staticmethod
    def _record(item) -> dict:
        return {
            name: _child(item, name)
            for name in (
                "title",
                "region",
                "country",
                "state",
                "skills",
                "category",
                "type",
                "description",
                "link",
                "guid",
                "pubDate",
            )
        }

    def normalize(self, raw):
        # Titles read "Company: Job title".
        company, separator, title = raw["title"].partition(": ")
        if not separator:
            raise ValueError("title is not 'Company: Title'")
        location = strings([raw["region"], raw["country"], raw["state"]])
        return self.job(
            raw,
            source_id=raw["guid"] or raw["link"],
            company=company,
            title=title,
            description_html=raw["description"],
            location=location,
            employment_type=employment(raw["type"]) if raw["type"] else [],
            posted_at=parsedate_to_datetime(raw["pubDate"]) if raw["pubDate"] else None,
            source_url=raw["link"],
            application_url=None,
            attribution="We Work Remotely",
            attribution_url="https://weworkremotely.com",
        )
