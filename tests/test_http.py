from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from job_scraper.http import CollectionError, HttpClient


class Clock:
    def __init__(self):
        self.now = 0
        self.waits = []

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


@pytest.mark.parametrize("failure", [429, 500, 502, 503, 504, 408, "timeout"])
def test_retries_recover(failure):
    calls = []
    clock = Clock()

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            if failure == "timeout":
                raise httpx.ReadTimeout("test", request=request)
            return httpx.Response(failure, headers={"Retry-After": "5"})
        return httpx.Response(200, json={"jobs": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        http = HttpClient(client, sleep=clock.sleep, clock=lambda: clock.now)
        assert http.get("https://example.test/jobs") == {"jobs": []}
    assert len(calls) == 2
    if failure != "timeout":
        assert sum(clock.waits) >= 5


@pytest.mark.parametrize("status", [401, 403, 404, 302])
def test_access_errors_and_redirects_not_retried(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(CollectionError, match=f"HTTP {status}"):
            HttpClient(client).get("https://example.test/jobs")
    assert len(calls) == 1


def test_retry_after_too_long_does_not_retry_early():
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(429, headers={"Retry-After": "86400"})
        )
    ) as client:
        with pytest.raises(CollectionError, match="wait budget"):
            HttpClient(client).get("https://example.test/jobs")


def test_retry_after_http_date():
    responses = iter(
        [
            httpx.Response(
                429,
                headers={"Retry-After": format_datetime(datetime.now(UTC) + timedelta(seconds=20))},
            ),
            httpx.Response(200, json={}),
        ]
    )
    clock = Clock()
    with httpx.Client(transport=httpx.MockTransport(lambda r: next(responses))) as client:
        HttpClient(client, sleep=clock.sleep, clock=lambda: clock.now).get(
            "https://example.test/jobs"
        )
    assert sum(clock.waits) >= 18


def test_exhausted_retries_and_pacing():
    clock = Clock()
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as client:
        http = HttpClient(client, retries=2, sleep=clock.sleep, clock=lambda: clock.now)
        with pytest.raises(CollectionError, match="exhausted"):
            http.get("https://example.test/jobs", interval=31)
    # Adding separately rounded retry and pacing waits can land just below 62.
    assert sum(clock.waits) >= 62 - 1e-9


def test_invalid_json_stops():
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>CAPTCHA</html>"))
    ) as client:
        with pytest.raises(CollectionError, match="Invalid JSON"):
            HttpClient(client).get("https://example.test/jobs")


def test_pacing_is_per_host():
    clock = Clock()
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    ) as client:
        http = HttpClient(client, sleep=clock.sleep, clock=lambda: clock.now)
        http.get("https://one.test/a", interval=2)
        http.get("https://one.test/b", interval=2)
        http.get("https://two.test/a", interval=2)
    assert clock.waits == [2]
