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
from .gemini import check_connection
from .http import CollectionError, HttpClient
from .matching import (
    analyze,
    approve_profile,
    candidates,
    export_profile,
    import_profile,
    latest_profile,
    rescore,
)
from .matching import (
    results as match_results,
)
from .processing import CATEGORIES, inspect_results, process_jobs
from .ranking import explain, render_explain, render_queue, render_rank, render_reviews
from .ranking import queue as rank_queue
from .ranking import rank as rank_jobs
from .registry import approve_board, effective_sources, set_enabled
from .reviews import STATUSES as REVIEW_STATUSES
from .reviews import add_review
from .reviews import history as review_history
from .runner import collect
from .storage import Store


def positive_int(value):
    parsed = int(value)
    if not 1 <= parsed <= 10000:
        raise argparse.ArgumentTypeError("must be between 1 and 10000")
    return parsed


def score_value(value):
    parsed = int(value)
    if not 0 <= parsed <= 100:
        raise argparse.ArgumentTypeError("must be between 0 and 100")
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
    gemini_check = sub.add_parser("gemini-check", help="Test Gemini using synthetic data only")
    gemini_check.add_argument("--model", help="Override GEMINI_MODEL for this check")
    profile = sub.add_parser("profile", help="Import and review a local PDF CV")
    profile.add_argument("action", choices=["import", "preview", "export", "approve"])
    profile.add_argument("--file", type=Path, help="PDF for import; reviewed JSON for approve")
    profile.add_argument("--output", type=Path, default=Path("data/candidate-profile-review.json"))
    matching = sub.add_parser("match", help="Preview or analyze technical matches")
    matching.add_argument("action", choices=["candidates", "analyze", "rescore", "results"])
    matching.add_argument("--live", action="store_true", help="Authorize billable Gemini requests")
    matching.add_argument("--limit", type=positive_int)
    matching.add_argument("--job-id", type=int)
    matching.add_argument("--include-history", action="store_true")
    matching.add_argument("--model", help="Override GEMINI_MODEL")
    ranking = sub.add_parser("rank", help="Rank current AI assessments locally; no API requests")
    ranking.add_argument("action", choices=["list", "explain", "queue"])
    ranking.add_argument("--job-id", type=int)
    ranking.add_argument("--eligibility", choices=["ELIGIBLE", "UNVERIFIED"])
    ranking.add_argument("--confidence", choices=["high", "medium", "low"])
    ranking.add_argument("--min-score", type=score_value)
    ranking.add_argument("--source", choices=sorted(COLLECTORS))
    ranking.add_argument("--assessment-status", choices=["stale", "missing"])
    shown = ranking.add_mutually_exclusive_group()
    shown.add_argument(
        "--hide-restricted", action="store_true", help="Omit the manually restricted section"
    )
    shown.add_argument(
        "--only-restricted", action="store_true", help="Show only manually restricted jobs"
    )
    ranking.add_argument("--include-history", action="store_true")
    ranking.add_argument("--limit", type=positive_int)
    ranking.add_argument("--model", help="Only count assessments made with this model")
    ranking.add_argument("--json", action="store_true", help="Print machine-readable output")
    dash = sub.add_parser(
        "dashboard", help="Run the local dashboard (127.0.0.1 only; makes no API requests)"
    )
    dash.add_argument("--host", default="127.0.0.1", help="loopback address only")
    dash.add_argument("--port", type=int, default=8765)
    review = sub.add_parser("review", help="View or record manual eligibility reviews")
    review.add_argument("action", choices=["list", "show", "add"])
    review.add_argument("--job-id", type=int)
    review.add_argument("--status", choices=list(REVIEW_STATUSES))
    review.add_argument("--explanation")
    review.add_argument(
        "--evidence",
        help="Your evidence reference (URL or quotation; not fetched or verified)",
    )
    review.add_argument(
        "--outdated", action="store_true", help="list: only reviews needing reverification"
    )
    review.add_argument("--json", action="store_true", help="Print machine-readable output")
    for item in (
        discovery,
        sources,
        jobs,
        migration,
        processing,
        results,
        profile,
        matching,
        ranking,
        review,
        dash,
    ):
        item.add_argument("--config", type=Path, default=Path("config.toml"))
    args = parser.parse_args(argv)
    if args.command == "gemini-check":
        output = check_connection(args.model)
        print(json.dumps(output, indent=2))
        return 0 if output["status"] == "connected" else 1
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        settings = load_settings(args.config)
        if args.command == "dashboard":
            from .dashboard.app import serve

            serve(settings.database, settings.matching, args.host, args.port)
            return 0
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
                elif args.command == "profile":
                    if args.action == "import":
                        if not args.file:
                            raise ValueError("profile import requires --file PDF")
                        output = import_profile(store, args.file)
                    elif args.action == "preview":
                        output = latest_profile(store)
                    elif args.action == "export":
                        output = export_profile(store, args.output)
                    else:
                        if not args.file:
                            raise ValueError("profile approve requires --file reviewed JSON")
                        output = approve_profile(store, args.file)
                elif args.command == "match":
                    if args.action == "candidates":
                        queue = candidates(
                            store, include_history=args.include_history, job_id=args.job_id
                        )
                        output = {
                            "count": len(queue),
                            "candidates": queue[: args.limit or 20],
                            "live_requests": 0,
                        }
                    elif args.action == "analyze":
                        output = analyze(
                            store,
                            settings.matching,
                            live=args.live,
                            limit=args.limit,
                            model=args.model,
                            include_history=args.include_history,
                            job_id=args.job_id,
                        )
                    elif args.action == "rescore":
                        output = rescore(
                            store,
                            settings.matching,
                            model=args.model,
                            include_history=args.include_history,
                            job_id=args.job_id,
                        )
                    else:
                        output = match_results(
                            store,
                            settings.matching,
                            include_history=args.include_history,
                            job_id=args.job_id,
                            model=args.model,
                        )
                elif args.command == "rank":
                    if args.action == "explain":
                        if args.job_id is None:
                            raise ValueError("rank explain requires --job-id")
                        data = explain(store, settings.matching, args.job_id, model=args.model)
                        output = data if args.json else render_explain(data)
                    elif args.action == "queue":
                        data = rank_queue(
                            store,
                            settings.matching,
                            include_history=args.include_history,
                            model=args.model,
                            status=args.assessment_status,
                            source=args.source,
                            limit=args.limit,
                        )
                        output = data if args.json else render_queue(data)
                    else:
                        data = rank_jobs(
                            store,
                            settings.matching,
                            include_history=args.include_history,
                            model=args.model,
                            eligibility=args.eligibility,
                            confidence=args.confidence,
                            min_score=args.min_score,
                            source=args.source,
                            restricted="hide"
                            if args.hide_restricted
                            else "only"
                            if args.only_restricted
                            else "show",
                            limit=args.limit,
                        )
                        output = data if args.json else render_rank(data)
                elif args.command == "review":
                    if args.action == "add":
                        if args.job_id is None or not args.status:
                            raise ValueError("review add requires --job-id and --status")
                        entry = add_review(
                            store, args.job_id, args.status, args.explanation, args.evidence
                        )
                        output = entry if args.json else render_reviews([entry])
                    else:
                        if args.action == "show" and args.job_id is None:
                            raise ValueError("review show requires --job-id")
                        entries = review_history(store, args.job_id)
                        if args.action == "list":
                            newest = {e["job_id"]: e for e in entries}
                            entries = sorted(newest.values(), key=lambda e: e["job_id"])
                            if args.outdated:
                                entries = [e for e in entries if e["currency"] != "current"]
                        output = entries if args.json else render_reviews(entries)
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
                text = (
                    output if isinstance(output, str) else json.dumps(output, indent=2, default=str)
                )
                try:
                    print(text)
                except UnicodeEncodeError:  # a console that cannot show every job-title character
                    print(text.encode("ascii", "replace").decode("ascii"))
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
