"""The Focus page: the simple main view of the dashboard.

Jobs are shown through the Module 3 final decision filters (Eligible, Unverified, Ineligible)
plus a To review tab: jobs the tool tagged only "good enough" (the `good_enough_fit` policy rule)
wait there for the user's answer. "Good fit" moves a job into the Eligible tab and "Not for me"
into the Ineligible tab (`feedback`, an append-only record of the user's own opinion; a pick
never changes a tag or an assessment and is kept as calibration evidence). A job marked as applied
leaves the filters and waits under Applied, using the existing application tracker (`tracking`);
nothing here applies for you. Pakistan hiring stays the separate Module 2 status on each job.
"""

from datetime import date

from .. import feedback
from . import data
from .render import badge, e, human, link_url, pager, query

TABS = ("REVIEW", "ELIGIBLE", "UNVERIFIED", "INELIGIBLE")
REVIEW_CODE = "good_enough_fit"
LABELS = {
    "REVIEW": "To review",
    "ELIGIBLE": "Eligible",
    "UNVERIFIED": "Unverified",
    "INELIGIBLE": "Ineligible",
    "APPLIED": "Applied",
}
HELP = {
    "ELIGIBLE": "Worth applying to: your CV supports the core of the role and nothing rules it out.",
    "UNVERIFIED": "Not settled either way. Often the CV does not show the main skill, which is not a rejection.",
    "INELIGIBLE": "A hard blocker was found, such as an experience minimum far above yours.",
    "REVIEW": "The tool says these are good enough to apply to, but some evidence is only partial. "
    "Say Good fit to move a job to Eligible, or Not for me to move it to Ineligible.",
    "APPLIED": "Jobs you applied to. Undo moves a job back to Saved and returns it to its filter.",
}
# A job leaves the decision filters once it is past "saved" or "planned".
SUBMITTED = frozenset({"APPLIED", "SCREENING", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN"})
STATUS_ORDER = ("APPLIED", "SCREENING", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN")


def tally(applications: dict[int, dict], today: date | None = None) -> dict:
    """How many jobs the user has applied to: in total, by status and in recent windows.

    A job counts once it has left SAVED, whatever its current status (a rejection still was an
    application). The windows use the application date; a missing date counts only in the total."""
    today = today or date.today()
    sent = [a for a in applications.values() if a["status"] in SUBMITTED]
    ages = []
    for a in sent:
        try:
            ages.append((today - date.fromisoformat(a["applied_on"])).days)
        except (TypeError, ValueError):
            continue
    return {
        "total": len(sent),
        "by_status": {k: sum(a["status"] == k for a in sent) for k in STATUS_ORDER},
        "last_7_days": sum(0 <= age < 7 for age in ages),
        "last_30_days": sum(0 <= age < 30 for age in ages),
        "saved": sum(a["status"] == "SAVED" for a in applications.values()),
    }


def tally_line(t: dict) -> str:
    stages = ", ".join(f"{k.title()} {v}" for k, v in t["by_status"].items() if v)
    line = (
        f"{t['total']} applied in total · {t['last_7_days']} in the last 7 days · "
        f"{t['last_30_days']} in the last 30 days"
    )
    return (
        line + (f" · {stages}" if stages else "") + (f" · {t['saved']} saved" if t["saved"] else "")
    )


LIST_ITEMS = 5


def params(raw: dict, sources: set[str]) -> dict:
    view = raw.get("d", "")
    source = (raw.get("source") or "").strip()
    try:
        page = max(1, int(raw.get("page") or 1))
    except ValueError:
        page = 1
    return {
        "d": view if view in (*TABS, "APPLIED") else "ELIGIBLE",
        "q": (raw.get("q") or "").strip()[:100],
        "source": source if source in sources else "",
        "page": page,
    }


def _short(text, limit):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _detail(snapshot, job_id) -> dict:
    """What a card shows when opened, from the stored judgment (never recomputed here)."""
    entry = snapshot.population_by_id.get(job_id) or {}
    judgment = entry.get("judgment")
    verdicts = entry.get("judgment_requirements") or []
    strengths, gaps = [], []
    for item in verdicts:
        if item["verdict"] in {"met", "partial"}:
            cv = item["cv_evidence"][0]["quote"] if item["cv_evidence"] else ""
            line = _short(item["job_quote"], 70) + (f"  (CV: {_short(cv, 60)})" if cv else "")
            strengths.append(line)
        elif item["verdict"] in {"unclear", "not_met"}:
            gaps.append(_short(item["job_quote"], 90))
    return {
        "summary": judgment["summary"] if judgment else "",
        "reasons": judgment["reason_codes"] if judgment else [],
        "strengths": strengths[:LIST_ITEMS],
        "gaps": gaps[:LIST_ITEMS],
        "judged_by": judgment["labeler"] if judgment else None,
        "carried": bool(judgment and judgment.get("carried_over_from")),
    }


def build(snapshot, applications: dict[int, dict], raw: dict, fit: dict | None = None) -> dict:
    fit = fit or {}
    sources = {s for r in snapshot.rows for s in r["sources"]}
    p = params(raw, sources)
    applied = {jid for jid, a in applications.items() if a["status"] in SUBMITTED}
    picked = {jid: e["verdict"] for jid, e in fit.items() if jid not in applied}

    def tab_of(r) -> str:
        """The tab a job sits in: your pick first, then the tool's tag (good-enough tags wait)."""
        pick = picked.get(r["job_id"])
        if pick == "good_fit":
            return "ELIGIBLE"
        if pick == "not_for_me":
            return "INELIGIBLE"
        if r["final_status"] == "ELIGIBLE" and r.get("final_code") == REVIEW_CODE:
            return "REVIEW"
        return r["final_status"]

    open_jobs = [
        r
        for r in snapshot.rows
        if r.get("final_status")
        and r["lifecycle"] == "active"
        and r["listed"]
        and r["job_id"] not in applied
    ]
    counts = {k: sum(tab_of(r) == k for r in open_jobs) for k in TABS}
    counts["APPLIED"] = len([j for j in applied if j in snapshot.by_id])
    if p["d"] == "APPLIED":
        pool = [snapshot.by_id[j] for j in applied if j in snapshot.by_id]
        pool.sort(
            key=lambda r: (applications[r["job_id"]].get("applied_on") or "", r["job_id"]),
            reverse=True,
        )
    else:
        pool = [r for r in open_jobs if tab_of(r) == p["d"]]
        pool.sort(key=lambda r: (-(r.get("score") or 0), r["job_id"]))
    needle = p["q"].casefold()
    pool = [
        r
        for r in pool
        if (not needle or needle in (r["title"] + " " + r["company"]).casefold())
        and (not p["source"] or p["source"] in r["sources"])
    ]
    pages = max(1, -(-len(pool) // data.PAGE_SIZE))
    page = min(p["page"], pages)
    start = (page - 1) * data.PAGE_SIZE
    items = [
        {
            **r,
            "application": applications.get(r["job_id"]),
            "fit": fit.get(r["job_id"]),
            "detail": _detail(snapshot, r["job_id"]),
        }
        for r in pool[start : start + data.PAGE_SIZE]
    ]
    return {
        "params": {**p, "page": page},
        "counts": counts,
        "total": len(pool),
        "page": page,
        "pages": pages,
        "items": items,
        "sources": sorted(sources),
        "not_judged": snapshot.counts["final"]["not_judged"],
        "tally": tally(applications),
    }


POLICY_CODES = frozenset({"out_of_scope", "good_enough_fit", "poor_fit"})


def _reason(item) -> str:
    detail = item["detail"]
    if item.get("final_code") in POLICY_CODES:
        text = human(item["final_code"])
    elif detail["reasons"]:
        text = ", ".join(human(code) for code in detail["reasons"][:2])
    else:
        text = human(item.get("final_code")) or "Not judged yet"
    return text[:1].upper() + text[1:]


def _tabs(result) -> str:
    p, counts = result["params"], result["counts"]
    links = []
    for key in (*TABS, "APPLIED"):
        on = " on" if p["d"] == key else ""
        side = " right" if key == "APPLIED" else ""
        href = "/?" + query(p, d=key, page=None)
        links.append(
            f'<a class="tab{on}{side}" href="{e(href)}">{LABELS[key]}'
            f'<span class="n">{counts[key]}</span></a>'
        )
    return '<nav class="tabs">' + "".join(links) + "</nav>"


def _search(result) -> str:
    p = result["params"]
    options = '<option value="">All sources</option>' + "".join(
        f"<option{' selected' if s == p['source'] else ''}>{e(s)}</option>"
        for s in result["sources"]
    )
    clear = f' <a class="small" href="/?d={e(p["d"])}">Clear</a>' if p["q"] or p["source"] else ""
    return (
        '<form class="tools" method="get" action="/">'
        f'<input type="hidden" name="d" value="{e(p["d"])}">'
        f'<input type="search" name="q" value="{e(p["q"])}" placeholder="Search title or company" '
        'aria-label="Search">'
        f'<select name="source" aria-label="Source">{options}</select>'
        f"<button>Search</button>{clear}</form>"
    )


def _form(item, csrf, next_path, action, name, value, label, title, klass="secondary"):
    return (
        f'<form class="apply" method="post" action="/jobs/{item["job_id"]}/{action}">'
        f'<input type="hidden" name="csrf" value="{e(csrf)}">'
        f'<input type="hidden" name="next" value="{e(next_path)}">'
        f'<input type="hidden" name="{name}" value="{value}">'
        f'<button class="{klass}" title="{e(title)}">{label}</button></form>'
    )


def _action(item, csrf, next_path, view) -> str:
    def fit(verdict, label, title, klass="secondary"):
        return _form(item, csrf, next_path, "fit", "verdict", verdict, label, title, klass)

    def status(value, label, title):
        return _form(item, csrf, next_path, "status", "status", value, label, title)

    pick = (item.get("fit") or {}).get("verdict")
    if view == "APPLIED":
        buttons = [status("SAVED", "Undo", "Moves the job back to Saved")]
    elif pick == "good_fit":
        buttons = [
            status("APPLIED", "Mark applied", "Moves the job to Applied"),
            fit("not_for_me", "Not for me", "Changes your answer: moves the job to Ineligible"),
            fit("cleared", "Undo", "Withdraws your answer; the tool's tag decides again"),
        ]
    elif pick == "not_for_me":
        buttons = [
            fit("good_fit", "Good fit", "Changes your answer: moves the job to Eligible"),
            fit("cleared", "Undo", "Withdraws your answer; the tool's tag decides again"),
        ]
    else:
        buttons = [
            fit("good_fit", "Good fit", "Confirms the job: moves it to Eligible"),
            fit("not_for_me", "Not for me", "Rejects the job: moves it to Ineligible"),
            status("APPLIED", "Mark applied", "Moves the job to Applied"),
        ]
    return '<div class="acts">' + "".join(buttons) + "</div>"


def _answer_form(item, csrf, next_path) -> str:
    """Opened card: tick why, add a note, then answer. The answer never changes a tag."""
    pick = item.get("fit") or {}
    chosen = set(pick.get("reason_list") or [])
    boxes = "".join(
        f'<label><input type="checkbox" name="reason" value="{key}"'
        f"{' checked' if key in chosen else ''}> {e(label)}</label>"
        for key, label in feedback.REASONS.items()
    )
    return (
        f'<form class="answer" method="post" action="/jobs/{item["job_id"]}/fit">'
        f'<input type="hidden" name="csrf" value="{e(csrf)}">'
        f'<input type="hidden" name="next" value="{e(next_path)}">'
        "<h4>Your answer (optional reason)</h4>"
        f'<div class="reasons">{boxes}</div>'
        f'<input type="text" name="note" maxlength="300" value="{e(pick.get("note") or "")}" '
        'placeholder="Optional note: why?" aria-label="Optional note">'
        '<div class="answers"><button class="secondary" name="verdict" value="good_fit">'
        'Good fit</button><button class="secondary" name="verdict" value="not_for_me">'
        "Not for me</button></div></form>"
    )


def _list(title, lines) -> str:
    if not lines:
        return ""
    return f"<div><h4>{e(title)}</h4><ul>{''.join(f'<li>{e(x)}</li>' for x in lines)}</ul></div>"


def _card(item, csrf, next_path, view) -> str:
    applied_tab = view == "APPLIED"
    detail, application = item["detail"], item["application"]
    status = item.get("final_status") or "NONE"
    score = (
        f'<div class="score"><b>{e(item["score"])}</b>fit score</div>'
        if item.get("score") is not None and item.get("assessment_status") == "current"
        else ""
    )
    sources = " · ".join(name.capitalize() for name in item["sources"])
    if applied_tab and application:
        tag = (
            f'<div class="why">{badge("info", application["status"])} '
            f'<span class="mute">applied {e(application["applied_on"] or "date not set")}</span></div>'
        )
    elif item.get("fit"):
        pick = item["fit"]
        was = (pick.get("final_status") or "no tag").capitalize()
        answer = feedback.LABELS[pick["verdict"]]
        why = ", ".join(feedback.REASONS[r] for r in pick.get("reason_list") or [])
        note = (f" · {why}" if why else "") + (f" · {e(pick['note'])}" if pick.get("note") else "")
        tag = (
            f'<div class="why">{badge("info", "You: " + answer)} '
            f'<span class="mute">the tool said {e(was)}{note}</span></div>'
        )
    else:
        tag = f'<div class="why"><span class="dot"></span>{e(_reason(item))}</div>'
    chips = (
        f'<span class="chip">Pakistan hiring (Module 2): {e(item["eligibility"])}'
        f"{' (stale)' if item['classification_stale'] else ''}</span>"
        f'<span class="chip">Experience: {e(item["experience"])}</span>'
        + (
            f'<span class="chip">Evidence coverage {e(item["coverage"])}%</span>'
            if item.get("coverage") is not None
            else ""
        )
    )
    summary = detail["summary"] or item.get("final_detail") or "No judgment text stored yet."
    body = (
        f"<p>{e(_short(summary, 520))}</p>"
        f'<div class="two">{_list("Evidence in your CV", detail["strengths"])}'
        f"{_list('Not shown in your CV', detail['gaps'])}</div>"
        f'<div class="chips">{chips}</div>'
        f"{_answer_form(item, csrf, next_path)}"
        f'<p class="small links"><a href="/jobs/{item["job_id"]}">Full details and notes</a> · '
        f"{link_url(item['source_url'], 'Open posting')} · {link_url(item['application_url'], 'Apply link')}"
        + (
            f' · <span class="mute">judged by {e(detail["judged_by"])}'
            + (" under an earlier profile; not yet re-judged with v3" if detail["carried"] else "")
            + "</span>"
            if detail["judged_by"]
            else ""
        )
        + "</p>"
    )
    return (
        f'<article class="job {status}"><details><summary>'
        f'<div class="main"><div class="title">{e(item["title"])}</div>'
        f'<div class="meta">{e(item["company"])} · {e(sources)} · #{item["job_id"]}</div>{tag}</div>'
        f'{score}</summary><div class="detail">{body}</div></details>'
        f"{_action(item, csrf, next_path, view)}</article>"
    )


def calibration_page(report) -> str:
    """Your Good fit / Not for me answers set against the tool's tag when you gave them."""
    if not report["picks"]:
        return (
            '<h1>Calibration</h1><p class="sub">No answers yet. Press Good fit or Not for me on '
            "a job; your answers are compared here with the tool's tag at that moment.</p>"
        )

    def pct(value):
        return "n/a" if value is None else f"{value}%"

    rows = "".join(
        f"<tr><td>{e(tag)}</td><td>{c['good_fit']}</td><td>{c['not_for_me']}</td></tr>"
        for tag, c in report["matrix"].items()
    )
    rules = "".join(
        f"<tr><td>{e(human(rule))}</td><td>{e(v['tag'])}</td><td>{v['good_fit']}</td>"
        f"<td>{v['not_for_me']}</td></tr>"
        for rule, v in report["by_rule"].items()
    )
    reasons_table = ""
    if report.get("by_reason"):
        reason_rows = "".join(
            f"<tr><td>{e(feedback.REASONS[r])}</td><td>{v['total']}</td>"
            f"<td>{e(', '.join(f'{t} {n}' for t, n in sorted(v['by_tag'].items())))}</td></tr>"
            for r, v in report["by_reason"].items()
        )
        reasons_table = (
            "<h4>Why you said Not for me</h4><table><tr><th>Reason</th><th>Answers</th>"
            "<th>By tool tag</th></tr>" + reason_rows + "</table>"
        )
    return (
        "<h1>Calibration</h1>"
        f'<p class="sub">{report["picks"]} answer(s). The tag is what the tool said when you answered; '
        "your answer never changes a tag.</p>"
        "<table><tr><th>Tool tag</th><th>Good fit</th><th>Not for me</th></tr>" + rows + "</table>"
        f"<p>Eligible tags you confirmed: <b>{pct(report['eligible_confirmed_pct'])}</b><br>"
        f"Ineligible tags you would still apply to (misses): "
        f"<b>{pct(report['ineligible_wanted_pct'])}</b><br>"
        f"Unverified jobs you wanted: <b>{pct(report['unverified_wanted_pct'])}</b><br>"
        f"Agreement on Eligible and Ineligible tags: <b>{pct(report['agreement_pct'])}</b></p>"
        "<h4>By rule</h4><table><tr><th>Rule</th><th>Tool tag</th><th>Good fit</th>"
        "<th>Not for me</th></tr>" + rules + "</table>" + reasons_table
    )


def focus_page(result, csrf) -> str:
    p, counts = result["params"], result["counts"]
    next_path = "/?" + query(p)
    applied_tab = p["d"] == "APPLIED"
    cards = "".join(_card(i, csrf, next_path, p["d"]) for i in result["items"])
    if not cards:
        nothing = (
            "No jobs match this search."
            if p["q"] or p["source"]
            else "No applied jobs yet."
            if applied_tab
            else "Nothing here yet."
        )
        cards = f'<div class="empty">{e(nothing)}</div>'
    waiting = result["not_judged"]
    more = (
        f'<p class="note">{waiting:,} relevant job{"s" if waiting != 1 else ""} have not been '
        'judged yet, so they are not listed here. <a href="/jobs?final=none">See them in Job Explorer</a>.</p>'
        if waiting
        else ""
    )
    tally_html = (
        f'<p class="sub"><b>{e(tally_line(result["tally"]))}</b></p>' if applied_tab else ""
    )
    return (
        f'<p class="sub">{counts["ELIGIBLE"]} eligible to apply to · {e(HELP[p["d"]])}</p>'
        + tally_html
        + _search(result)
        + _tabs(result)
        + cards
        + pager("/", {k: v for k, v in p.items() if k != "page"}, result["page"], result["pages"])
        + more
        + '<p class="note">Eligible, Unverified and Ineligible are suitability for applying (Module 3). '
        "They are never employer-confirmed hiring in Pakistan; that is the separate Module 2 status "
        "on each job.</p>"
    )
