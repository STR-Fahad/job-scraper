import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

import httpx
from pydantic import ValidationError

from .collectors import COLLECTORS
from .config import load_settings
from .discovery import discover
from .http import CollectionError, HttpClient
from .processing import CATEGORIES, inspect_results, process_jobs
from .registry import approve_board, effective_sources, set_enabled
from .runner import collect
from .storage import Store


def positive_int(value):
    parsed = int(value)
    if not 1 <= parsed <= 10000:
        raise argparse.ArgumentTypeError("must be between 1 and 10000")
    return parsed


def main(argv=None):
    parser = argparse.ArgumentParser(description="Collect public job APIs into SQLite")
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("collect")
    choice = command.add_mutually_exclusive_group(required=True)
    choice.add_argument("--source", choices=sorted(COLLECTORS))
    choice.add_argument("--all", action="store_true", help="Run all enabled registry entries")
    command.add_argument("--board", help="Limit to one configured employer board")
    command.add_argument("--config", type=Path, default=Path("config.toml"))
    command.add_argument("--max-pages", type=positive_int)
    command.add_argument("--json", action="store_true", help="Print machine-readable statistics")
    discovery = sub.add_parser(
        "discover", help="Verify boards from a supplied ATS or permitted careers URL"
    )
    discovery.add_argument("--url", required=True)
    discovery.add_argument("--company", required=True)
    discovery.add_argument("--allow-careers-fetch", action="store_true")
    discovery.add_argument("--approve-verified", action="store_true")
    sources = sub.add_parser("sources", help="Inspect and manage registry entries")
    sources.add_argument("action", choices=["list", "enable", "disable", "approve"])
    sources.add_argument("--source", choices=sorted(COLLECTORS))
    sources.add_argument("--board", default="")
    sources.add_argument("--region", choices=["global", "eu"], default="global")
    jobs = sub.add_parser("jobs", help="List active, listed jobs; history is opt-in")
    jobs.add_argument("--include-history", action="store_true")
    migration = sub.add_parser("migrate", help="Back up and safely migrate the configured database")
    processing = sub.add_parser(
        "process", help="Classify stored jobs for a Pakistan-based 0–3-year profile"
    )
    results = sub.add_parser(
        "results", help="Filter classifications with evidence; stale results stay unverified"
    )
    results.add_argument("--status", choices=["ELIGIBLE", "INELIGIBLE", "UNVERIFIED"])
    results.add_argument("--category", choices=list(CATEGORIES))
    results.add_argument("--job-id", type=int)
    results.add_argument("--include-history", action="store_true")
    for item in (discovery, sources, jobs, migration, processing, results):
        item.add_argument("--config", type=Path, default=Path("config.toml"))
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        settings = load_settings(args.config)
        if args.command != "collect":
            with Store(settings.database) as store:
                if args.command == "jobs":
                    output = store.list_jobs(args.include_history)
                elif args.command == "process":
                    output = process_jobs(store)
                elif args.command == "results":
                    output = inspect_results(
                        store,
                        status=args.status,
                        category=args.category,
                        job_id=args.job_id,
                        include_history=args.include_history,
                    )
                elif args.command == "migrate":
                    output = {
                        "schema_version": store.db.execute("PRAGMA user_version").fetchone()[0],
                        "jobs": store.db.execute("SELECT count(*) FROM jobs").fetchone()[0],
                        "integrity": store.db.execute("PRAGMA integrity_check").fetchone()[0],
                    }
                elif args.command == "sources":
                    if args.action == "list":
                        output = {
                            "effective": [
                                c.model_dump(mode="json")
                                for c in effective_sources(store, settings.sources)
                            ],
                            "discovered": [
                                dict(r) for r in store.db.execute("SELECT * FROM company_boards")
                            ],
                        }
                    else:
                        if not args.source:
                            raise ValueError("--source is required for registry changes")
                        if args.action == "approve":
                            approve_board(store, args.source, args.board, args.region)
                        else:
                            set_enabled(
                                store,
                                settings.sources,
                                args.source,
                                args.board,
                                args.action == "enable",
                                args.region,
                            )
                        output = {"action": args.action, "source": args.source, "board": args.board}
                else:
                    with httpx.Client(
                        timeout=settings.timeout_seconds,
                        follow_redirects=False,
                        headers={"User-Agent": "personal-job-scraper/0.1"},
                    ) as client:
                        http = HttpClient(
                            client,
                            retries=settings.retries,
                            max_retry_delay=settings.max_retry_delay,
                            cooldown_store=store,
                        )
                        output = discover(
                            store,
                            http,
                            args.url,
                            args.company,
                            allow_careers_fetch=args.allow_careers_fetch,
                            approve_verified=args.approve_verified,
                        )
                print(json.dumps(output, indent=2, default=str))
                return 0
        stats = collect(settings, source=args.source, board=args.board, max_pages=args.max_pages)
    except (OSError, ValueError, ValidationError, sqlite3.Error, CollectionError) as exc:
        print(f"Configuration/storage error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted; already saved jobs are retained.", file=sys.stderr)
        return 130
    if args.json:
        print(json.dumps([{**s.model_dump(), "rejected": s.rejected} for s in stats], indent=2))
    else:
        for stat in stats:
            print(" ".join(f"{k}={v}" for k, v in stat.model_dump().items()))
        print(
            f"Total: fetched={sum(s.fetched for s in stats)} "
            f"inserted={sum(s.inserted for s in stats)} updated={sum(s.updated for s in stats)} "
            f"unchanged={sum(s.unchanged for s in stats)} errors={sum(s.errors for s in stats)}"
        )
    return 1 if any(s.errors or s.invalid for s in stats) else 0
