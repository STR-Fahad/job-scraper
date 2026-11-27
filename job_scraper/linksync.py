"""Start-up link sync: import the cloud checker's results and re-check what it could not judge.

Runs in the background when the dashboard starts. Nothing here calls Gemini or any paid API.
The cloud results come from the private links repo (`work/links-repo`); the laptop fills in the
sources the cloud could not reach (the "unknown" ones) and publishes a fresh export.

Every step is best-effort: a failure is logged and the next start tries again. A removed
posting still needs two checks on different days before it closes (see `linkcheck.decide_close`).
"""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from .linkcheck import check_links, export_links, import_results

GIT_TIMEOUT = 180
RECHECK_LIMIT = 300
LOG_NAME = "link_sync.log"


def links_repo(database) -> Path | None:
    """The private links repo next to the database's project, or None when it is not set up."""
    repo = Path(database).resolve().parent.parent / "work" / "links-repo"
    return repo if (repo / ".git").exists() else None


def _git(repo, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
        check=False,
    )


def _step(report, name, action):
    """Run one step; record its outcome or its error and carry on."""
    try:
        report[name] = action()
    except Exception as exc:  # best-effort: never stop the dashboard or the next step
        report[name] = f"failed: {type(exc).__name__}: {str(exc)[:160]}"


def _pull(repo):
    done = _git(repo, "pull", "--rebase", "--autostash", "origin", "main")
    if done.returncode:
        raise RuntimeError((done.stderr or done.stdout).strip().splitlines()[-1:] or "git pull")
    return "up to date" if "Already up to date" in done.stdout else "pulled"


def _import(settings, repo):
    results = repo / "results"
    if not results.exists():
        return "no results folder"
    report = import_results(settings, [results], apply=True)
    return {"recorded": report["recorded"], **report["summary"]}


def _recheck(settings):
    totals = check_links(settings, apply=True, limit=RECHECK_LIMIT, only_unknown=True)["totals"]
    return {key: totals.get(key, 0) for key in ("checked", "live", "removed_first_check", "closed")}


def _publish(settings, repo):
    data = export_links(settings)
    target = repo / "export.json"
    old = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    if [i["url"] for i in old.get("items", [])] == [i["url"] for i in data["items"]]:
        return _push_pending(repo) or "export unchanged"
    target.write_text(json.dumps(data, indent=1), encoding="utf-8")
    _git(repo, "add", "export.json")
    committed = _git(repo, "commit", "-m", "Refresh export")
    if committed.returncode:
        raise RuntimeError("git commit failed")
    pushed = _git(repo, "push", "origin", "main")
    if pushed.returncode:
        raise RuntimeError("git push failed (committed locally; pushes on the next start)")
    return f"exported {len(data['items'])} links"


def _push_pending(repo) -> str:
    """Push a commit an earlier start made but could not push (empty when nothing waits)."""
    ahead = _git(repo, "rev-list", "--count", "origin/main..HEAD")
    if ahead.returncode or ahead.stdout.strip() in ("", "0"):
        return ""
    if _git(repo, "push", "origin", "main").returncode:
        raise RuntimeError("git push failed (committed locally; pushes on the next start)")
    return "pushed an earlier export"


def sync_links(settings, *, repo=None, now=None) -> dict:
    """Pull, import, re-check the unknowns, publish the export. Returns what each step did."""
    now = now or datetime.now(UTC)
    repo = repo or links_repo(settings.database)
    if repo is None:
        return {"skipped": "no links repo (work/links-repo)", "at": now.isoformat()}
    report = {"at": now.isoformat()}
    _step(report, "pull", lambda: _pull(repo))
    _step(report, "import", lambda: _import(settings, repo))
    _step(report, "recheck_unknown", lambda: _recheck(settings))
    _step(report, "publish", lambda: _publish(settings, repo))
    return report


def summary(report) -> str:
    """One line for the dashboard and the log."""
    if "skipped" in report:
        return f"Link sync skipped: {report['skipped']}."
    failed = [f"{k}: {v}" for k, v in report.items() if isinstance(v, str) and "failed" in v]
    closed = 0
    for key in ("import", "recheck_unknown"):
        value = report.get(key)
        if isinstance(value, dict):
            closed += value.get("closed", 0) or value.get("would_close", 0)
    line = f"Link sync done: {closed} posting{'s' if closed != 1 else ''} closed."
    return line + (" Problems: " + "; ".join(failed) if failed else "")


def run_and_log(settings, *, repo=None, say=print) -> dict:
    """Run the sync, write one log line and tell `say` the one-line result."""
    report = sync_links(settings, repo=repo)
    line = summary(report)
    log = Path(settings.database).parent / LOG_NAME
    try:
        with log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(report, default=str) + "\n")
    except OSError:
        pass
    say(line)
    return report
