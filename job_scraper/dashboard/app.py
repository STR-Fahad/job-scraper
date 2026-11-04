"""Local WSGI application and server. Loopback only; no telemetry; never calls Gemini.

Pages are read-only views over SQLite. The only writes are the user's own application
tracking (`tracking`) and appended manual eligibility reviews (`reviews`), both through the
existing writer lock. Nothing here starts a collector or an assessment.
"""

import hmac
import secrets
import socketserver
import sys
import time
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from .. import ranking, reviews, tracking
from ..config import MatchingConfig
from ..storage import Store
from . import data, render

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
MAX_BODY = 64 * 1024
STATUS_TEXT = {200: "200 OK", 303: "303 See Other", 400: "400 Bad Request", 403: "403 Forbidden"}
HEADERS = [
    ("Content-Type", "text/html; charset=utf-8"),
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "same-origin"),
    ("X-Frame-Options", "DENY"),
    (
        "Content-Security-Policy",
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'",
    ),
]


class Dashboard:
    def __init__(self, database, matching: MatchingConfig | None = None, port=8765):
        self.database = database
        self.matching = matching or MatchingConfig()
        self.port = port
        self.csrf = secrets.token_urlsafe(24)
        self.cache = data.Cache(database, self.matching)
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}

    # --- infrastructure ---------------------------------------------------------------

    def prepare(self) -> None:
        """Create the additive tracking tables if needed (backed up first), then warm the cache."""
        with Store(self.database) as store:
            tracking.ensure_schema(store)
        self.cache.warm()

    def __call__(self, environ, start_response):
        try:
            status, headers, body = self.dispatch(environ)
        except Exception as exc:  # never leak a traceback or data to the browser
            print(f"dashboard error: {type(exc).__name__}: {exc}", file=sys.stderr)
            status, headers, body = self.error_page(500, "Something went wrong. See the console.")
        start_response(status, headers)
        return [body]

    def respond(self, html, code=200, location=None):
        headers = list(HEADERS)
        if location:
            headers.append(("Location", location))
        status = STATUS_TEXT.get(code) or f"{code} Error"
        return status, headers, html.encode("utf-8")

    def error_page(self, code, message):
        body = render.layout("Error", "", f'<div class="notice err">{render.e(message)}</div>')
        return self.respond(body, code)

    def ro(self):
        return data.connect_readonly(self.database)

    def dispatch(self, environ):
        host = environ.get("HTTP_HOST", "")
        if host not in self.hosts:
            return self.error_page(403, "Unexpected Host header; this dashboard is local only.")
        method = environ["REQUEST_METHOD"]
        path = environ.get("PATH_INFO", "/")
        query = {
            k: v[-1]
            for k, v in parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True).items()
        }
        if method == "POST":
            return self.post(environ, path)
        if method not in {"GET", "HEAD"}:
            return self.error_page(400, "Method not allowed.")
        return self.get(path, query)

    # --- GET ------------------------------------------------------------------------------

    def page(self, title, active, body, query, banner=None):
        return self.respond(
            render.layout(
                title,
                active,
                body,
                notice=query.get("notice"),
                error=query.get("error"),
                banner=banner,
            )
        )

    def get(self, path, query):
        snapshot, refreshing = self.cache.current()
        if snapshot is None:
            return self.error_page(
                503, self.cache.error or "The database is not available yet. Try again shortly."
            )
        banner = None
        if refreshing:
            banner = (
                "Stored data changed; counts and lists are refreshing and may be a few seconds old."
            )
        elif self.cache.error:
            banner = f"Showing the previous data. Last refresh problem: {self.cache.error}"
        db = self.ro()
        try:
            if path == "/":
                body = render.overview(
                    snapshot,
                    tracking.counts(db),
                    data.approved_profile_summary(db),
                    tracking.orphans(db),
                )
                return self.page("Overview", "overview", body, query, banner)
            if path == "/jobs":
                result = data.explore(snapshot, tracking.statuses_by_job(db), query)
                sources = sorted({s for r in snapshot.rows for s in r["sources"]})
                return self.page(
                    "Job Explorer", "jobs", render.explorer(result, sources), query, banner
                )
            if path.startswith("/jobs/"):
                return self.job(path, snapshot, query, banner)
            if path == "/rankings":
                return self.rankings(snapshot, query, banner)
            if path == "/reviews":
                result = data.review_queue(self.database, snapshot, query)
                return self.page(
                    "Eligibility Review Queue",
                    "reviews",
                    render.review_queue_page(result),
                    query,
                    banner,
                )
            if path == "/applications":
                return self.applications(db, query, banner)
        finally:
            db.close()
        return self.error_page(404, "Page not found.")

    def job(self, path, snapshot, query, banner):
        try:
            job_id = int(path.split("/")[2])
        except (IndexError, ValueError):
            return self.error_page(404, "Job not found.")
        detail = data.job_detail(self.database, self.matching, snapshot, job_id)
        if detail is None:
            return self.error_page(404, f"No job with id {job_id}.")
        title = f"{detail['job']['title']}"
        body = render.job_page(detail, self.csrf, f"/jobs/{job_id}")
        return self.page(title, "jobs", body, query, banner)

    def rankings(self, snapshot, query, banner):
        db = self.ro()
        try:
            store = data.ReadStore(db)
            kwargs = {
                "eligibility": query.get("eligibility") or None,
                "confidence": query.get("confidence") or None,
            }
            if kwargs["eligibility"] not in {None, "ELIGIBLE", "UNVERIFIED"}:
                kwargs["eligibility"] = None
            if kwargs["confidence"] not in {None, "high", "medium", "low"}:
                kwargs["confidence"] = None
            ranked = ranking.rank(store, self.matching, population=snapshot.population, **kwargs)
            queue = ranking.queue(store, self.matching, population=snapshot.population)
        finally:
            db.close()
        body = render.rankings(ranked, queue, query, None)
        return self.page("CV Matches & Rankings", "rankings", body, query, banner)

    def applications(self, db, query, banner):
        params = {
            "q": (query.get("q") or "").strip(),
            "status": (query.get("status") or "").strip(),
        }
        try:
            page = max(1, int(query.get("page") or 1))
        except ValueError:
            page = 1
        try:
            listing = tracking.list_applications(
                db,
                status=params["status"] or None,
                query=params["q"],
                limit=data.PAGE_SIZE,
                offset=(page - 1) * data.PAGE_SIZE,
            )
        except ValueError as exc:
            return self.page(
                "My Applications", "applications", "", {**query, "error": str(exc)}, banner
            )
        pages = max(1, -(-listing["total"] // data.PAGE_SIZE))
        body = render.applications_page(listing, tracking.counts(db), params, page, pages)
        return self.page("My Applications", "applications", body, query, banner)

    # --- POST -----------------------------------------------------------------------------

    def post(self, environ, path):
        origin = environ.get("HTTP_ORIGIN")
        if origin and urlsplit(origin).netloc not in self.hosts:
            return self.error_page(403, "Cross-site request refused.")
        try:
            size = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            size = MAX_BODY + 1
        if size > MAX_BODY:
            return self.error_page(400, "Request too large.")
        form = {
            k: v[-1]
            for k, v in parse_qs(
                environ["wsgi.input"].read(size).decode("utf-8", "replace")
            ).items()
        }
        if not hmac.compare_digest(form.get("csrf", ""), self.csrf):
            return self.error_page(403, "Invalid or missing form token. Reload the page and retry.")
        parts = path.strip("/").split("/")
        target = self.safe_next(form.get("next"), "/applications")
        if len(parts) != 3 or parts[0] != "jobs" or not parts[1].isdigit():
            return self.error_page(404, "Unknown action.")
        job_id, action = int(parts[1]), parts[2]
        try:
            message = self.write(job_id, action, form)
            return self.respond("", 303, self.redirect(target, notice=message))
        except ValueError as exc:
            return self.respond("", 303, self.redirect(target, error=str(exc)))
        except OSError as exc:
            return self.respond(
                "", 303, self.redirect(target, error=f"Database is busy ({exc}); try again.")
            )

    def write(self, job_id, action, form) -> str:
        for attempt in range(40):  # a background refresh may hold the writer lock briefly
            try:
                store = Store(self.database)
                break
            except OSError:
                if attempt == 39:
                    raise
                time.sleep(0.25)
        with store:
            if action == "save":
                record = tracking.save_job(store, job_id)
                return "Saved." if record["created"] else "Already tracked; nothing changed."
            if action == "status":
                result = tracking.set_status(
                    store,
                    job_id,
                    form.get("status", ""),
                    applied_on=form.get("applied_on"),
                    note=form.get("note"),
                )
                return f"Status is {result['status']}." if result["changed"] else "No change."
            if action == "details":
                result = tracking.update_details(
                    store,
                    job_id,
                    follow_up_on=form.get("follow_up_on", ""),
                    channel=form.get("channel", ""),
                    reference=form.get("reference", ""),
                    notes=form.get("notes", ""),
                )
                return "Details saved." if result["changed"] else "No change."
            if action == "review":
                entry = reviews.add_review(
                    store,
                    job_id,
                    form.get("status", ""),
                    form.get("explanation", ""),
                    form.get("evidence") or None,
                )
                return (
                    f"Review #{entry['id']} appended ({entry['status']}). Module 2 and the score "
                    "are unchanged."
                )
        raise ValueError("Unknown action")

    @staticmethod
    def safe_next(value, default):
        if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
            return value.split("#")[0]
        return default

    @staticmethod
    def redirect(target, **message) -> str:
        sep = "&" if "?" in target else "?"
        clean = {k: v[:300] for k, v in message.items() if v}
        return target + sep + urlencode(clean, quote_via=quote)


class _Server(socketserver.ThreadingMixIn, WSGIServer):
    daemon_threads = True


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):  # request lines can contain search text; keep them local
        return


def serve(database, matching, host="127.0.0.1", port=8765, ready=None):
    if host not in LOOPBACK:
        raise ValueError("The dashboard only binds to a loopback address (127.0.0.1)")
    app = Dashboard(database, matching, port)
    print(f"Preparing the dashboard (reading {database}) ...", flush=True)
    app.prepare()
    server = make_server(
        "::1" if host == "::1" else host,
        port,
        app,
        server_class=_Server,
        handler_class=_QuietHandler,
    )
    print(f"Job Scraper dashboard: http://127.0.0.1:{port}/   (Ctrl+C to stop)", flush=True)
    if ready:
        ready(server, app)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        server.server_close()
