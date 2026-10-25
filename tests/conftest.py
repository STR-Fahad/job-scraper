from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from job_scraper.collectors import COLLECTORS
from job_scraper.config import SourceConfig
from job_scraper.http import HttpClient
from job_scraper.models import Statistics


def pytest_configure(config):
    # Keep test scratch files inside the repository, including on a fresh clone.
    Path(config.rootpath, "work").mkdir(exist_ok=True)


ROWS = {
    "himalayas": {
        "guid": "https://himalayas.app/companies/acme/jobs/engineer",
        "title": "Engineer",
        "companyName": "Acme",
        "description": "<p>Full description</p>",
        "locationRestrictions": [{"name": "Pakistan", "alpha2": "PK", "slug": "pakistan"}],
        "timezoneRestrictions": ["UTC+5"],
        "employmentType": "Full Time",
        "minSalary": 40,
        "maxSalary": 60,
        "currency": "USD",
        "salaryPeriod": "hourly",
        "pubDate": 1700000000,
        "applicationLink": "https://himalayas.app/apply/1",
    },
    "greenhouse": {
        "id": 1,
        "title": "Engineer",
        "content": "&lt;p&gt;Full description&lt;/p&gt;",
        "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
        "location": {"name": "London"},
        "updated_at": "2026-01-01T00:00:00Z",
    },
    "ashby": {
        "title": "Engineer",
        "descriptionHtml": "<p>Full description</p>",
        "jobUrl": "https://jobs.ashbyhq.com/acme/one",
        "applyUrl": "https://jobs.ashbyhq.com/acme/one/apply",
        "location": "London",
        "secondaryLocations": [{"location": "Paris"}],
        "employmentType": "FullTime",
        "isRemote": True,
        "publishedAt": "2026-01-01T00:00:00Z",
        "compensation": {
            "scrapeableCompensationSalarySummary": "$50K-$60K",
            "compensationTiers": [],
        },
    },
    "lever": {
        "id": "one",
        "text": "Engineer",
        "description": "<p>Full description</p>",
        "lists": [{"text": "Requirements", "content": "<li>Python</li>"}],
        "additional": "<p>Closing section</p>",
        "hostedUrl": "https://jobs.lever.co/acme/one",
        "applyUrl": "https://jobs.lever.co/acme/one/apply",
        "categories": {
            "location": "London",
            "allLocations": ["London", "Paris"],
            "commitment": "Full-time",
        },
        "salaryRange": {"min": 10, "max": 20, "currency": "USD", "interval": "hourly"},
    },
    "remotive": {
        "id": 1,
        "title": "Engineer",
        "company_name": "Acme",
        "description": "<p>Full description</p>",
        "url": "https://remotive.com/remote-jobs/engineer-1",
        "candidate_required_location": "Worldwide",
        "job_type": "full_time",
        "salary": "$50K-$60K",
        "publication_date": "2026-01-01T00:00:00",
    },
    "arbeitnow": {
        "slug": "engineer-1",
        "title": "Engineer",
        "company_name": "Acme",
        "description": "&lt;p&gt;Full description&lt;/p&gt;",
        "url": "https://www.arbeitnow.com/jobs/engineer-1",
        "location": "Berlin",
        "remote": False,
        "job_types": ["full-time"],
        "created_at": 1700000000,
    },
}


@pytest.fixture
def rows():
    return deepcopy(ROWS)


@pytest.fixture
def make_collector():
    clients = []

    def make(source, responses=None, handler=None, **overrides):
        config = {"source": source, "fetch_details": False}
        if source in {"greenhouse", "ashby", "lever"}:
            config.update(board="acme", company="Acme")
        config.update(overrides)
        calls = []
        queue = iter(responses or [])

        def respond(request):
            calls.append(request)
            if handler:
                return handler(request)
            result = next(queue)
            return (
                result if isinstance(result, httpx.Response) else httpx.Response(200, json=result)
            )

        client = httpx.Client(transport=httpx.MockTransport(respond))
        clients.append(client)
        http = HttpClient(client, retries=0, sleep=lambda _: None)
        stats = Statistics(source=source)
        return COLLECTORS[source](SourceConfig(**config), http, stats), calls, stats

    yield make
    for client in clients:
        client.close()
