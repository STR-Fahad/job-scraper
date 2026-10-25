"""Synchronous, injectable transport with host pacing and bounded retries."""

import logging
import math
import random
import time
from datetime import UTC
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)


class CollectionError(RuntimeError):
    pass


class HttpClient:
    def __init__(
        self,
        client: httpx.Client,
        *,
        retries=3,
        max_retry_delay=120,
        sleep=time.sleep,
        clock=time.monotonic,
        wall_clock=time.time,
        cooldown_store=None,
    ):
        self.client = client
        self.retries = retries
        self.max_retry_delay = max_retry_delay
        self.sleep = sleep
        self.clock = clock
        self.wall_clock = wall_clock
        self.cooldown_store = cooldown_store
        self.not_before: dict[str, float] = {}
        self.last_request: dict[str, float] = {}

    def get(self, url, *, params=None, headers=None, interval=2, as_text=False):
        host = urlsplit(url).netloc
        for attempt in range(self.retries + 1):
            wait = self.not_before.get(host, 0) - self.clock()
            if self.cooldown_store:
                wait = max(wait, self.cooldown_store.host_deadline(host) - self.wall_clock())
            if wait > min(30, self.max_retry_delay):
                raise CollectionError(f"{host}: Retry-After exceeds wait budget; host deferred")
            if wait > 0:
                self.sleep(wait)
            remaining = self.last_request.get(host, -float("inf")) + interval - self.clock()
            if remaining > 0:
                self.sleep(remaining)
            self.last_request[host] = self.clock()
            response = None
            try:
                response = self.client.get(url, params=params, headers=headers)
                self.record_retry_after(host, response.headers.get("Retry-After"))
                if response.status_code not in {408, 429, 500, 502, 503, 504}:
                    if not response.is_success:
                        raise CollectionError(
                            f"HTTP {response.status_code} from {host}; no bypass attempted"
                        )
                    try:
                        if as_text:
                            if len(response.content) > 2_000_000:
                                raise CollectionError(f"{host}: page exceeds discovery size limit")
                            return response.text
                        return response.json()
                    except ValueError as exc:
                        raise CollectionError(f"Invalid JSON from {host}") from exc
                reason = f"HTTP {response.status_code}"
            except httpx.TransportError as exc:
                reason = type(exc).__name__
            if attempt == self.retries:
                raise CollectionError(f"{host}: {reason}; retries exhausted")
            delay = min(self.max_retry_delay, 2**attempt + random.uniform(0, 0.5))
            if response is not None and response.headers.get("Retry-After"):
                requested = self.not_before.get(host, 0) - self.clock()
                if requested > min(30, self.max_retry_delay):
                    raise CollectionError(
                        f"{host}: Retry-After exceeds wait budget; stopping source"
                    )
                delay = max(delay, requested)
            logger.warning(
                "Retry %s/%s host=%s reason=%s delay=%.1fs",
                attempt + 1,
                self.retries,
                host,
                reason,
                delay,
            )
            self.sleep(delay)
        raise AssertionError("unreachable")

    def record_retry_after(self, host, raw):
        if not raw:
            return
        try:
            delay = float(raw)
        except ValueError:
            try:
                date = parsedate_to_datetime(raw)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=UTC)
                delay = date.timestamp() - self.wall_clock()
            except (ValueError, TypeError, OverflowError):
                return
        if not math.isfinite(delay) or delay <= 0:
            return
        self.not_before[host] = max(self.not_before.get(host, 0), self.clock() + delay)
        if self.cooldown_store:
            self.cooldown_store.defer_host(host, self.wall_clock() + delay)
