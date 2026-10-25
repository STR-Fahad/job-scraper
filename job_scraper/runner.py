import logging

import httpx

from .collectors import COLLECTORS
from .config import Settings
from .http import CollectionError, HttpClient
from .models import Statistics
from .registry import effective_sources
from .storage import Store

logger = logging.getLogger(__name__)


def collect(
    settings: Settings, *, source=None, board=None, max_pages=None, client=None, sleep=None
) -> list[Statistics]:
    configs = [
        c
        for c in settings.sources
        if c.enabled
        and (source is None or c.source == source)
        and (board is None or c.board == board)
    ]
    if not configs and not settings.database.exists():
        raise ValueError("No enabled sources match; configure employer boards in the registry")
    owns_client = client is None
    client = client or httpx.Client(
        timeout=httpx.Timeout(settings.timeout_seconds),
        follow_redirects=False,
        headers={"User-Agent": "personal-job-scraper/0.1", "Accept": "application/json"},
    )
    kwargs = {"sleep": sleep} if sleep is not None else {}
    http = HttpClient(
        client, retries=settings.retries, max_retry_delay=settings.max_retry_delay, **kwargs
    )
    results = []
    try:
        with Store(settings.database) as store:
            http.cooldown_store = store
            configs = [
                c
                for c in effective_sources(store, settings.sources)
                if c.enabled
                and (source is None or c.source == source)
                and (board is None or c.board == board)
            ]
            if not configs:
                raise ValueError(
                    "No enabled sources match; configure employer boards in the registry"
                )
            for config in configs:
                if max_pages is not None:
                    config = config.model_copy(update={"max_pages": max_pages})
                stats = Statistics(source=config.source, board=config.scope)
                results.append(stats)
                if not store.claim_run(config.source, config.scope, config.cooldown_seconds):
                    stats.skipped = True
                    logger.info(
                        "Cooldown: skipping source=%s board=%s", config.source, config.scope
                    )
                    store.save_statistics(stats)
                    continue
                logger.info("Collecting source=%s board=%s", config.source, config.scope)
                collector = COLLECTORS[config.source](config, http, stats)
                scan_id = store.begin_scan(config.source, config.scope)
                try:
                    for job in collector.collect():
                        result = store.upsert(job, scan_id=scan_id)
                        setattr(stats, result.action, getattr(stats, result.action) + 1)
                        stats.duplicates += int(result.duplicate)
                        stats.merged += result.merged
                        stats.expired += int(result.expired)
                        stats.hidden += int(not job.is_listed)
                except CollectionError as exc:
                    stats.errors += 1
                    logger.error(
                        "Collection failed source=%s board=%s: %s", config.source, config.scope, exc
                    )
                except (ValueError, TypeError, KeyError, AttributeError) as exc:
                    stats.errors += 1
                    logger.error(
                        "Unexpected response source=%s board=%s type=%s",
                        config.source,
                        config.scope,
                        type(exc).__name__,
                    )
                if stats.truncated:
                    logger.warning(
                        "Page limit reached source=%s board=%s; results incomplete",
                        config.source,
                        config.scope,
                    )
                store.finish_scan(
                    scan_id, stats, empty_confirmation_seconds=config.empty_confirmation_seconds
                )
                store.save_statistics(stats)
    finally:
        if owns_client:
            client.close()
    return results
