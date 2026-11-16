"""The Focus page: the simple main view of the dashboard.

Jobs are shown only through the Module 3 final decision filters (Eligible, Unverified,
Ineligible). A job marked as applied leaves those filters and waits under Applied, using the
existing application tracker (`tracking`): nothing new is stored and nothing here applies for
you. Pakistan hiring stays the separate Module 2 status shown on each job.
"""

from . import data
from .render import badge, e, human, link_url, pager, query

TABS = ("ELIGIBLE", "UNVERIFIED", "INELIGIBLE")
LABELS = {
    "ELIGIBLE": "Eligible",
    "UNVERIFIED": "Unverified",
    "INELIGIBLE": "Ineligible",
    "APPLIED": "Applied",
}
HELP = {
    "ELIGIBLE": "Worth applying to: your CV supports the core of the role and nothing rules it out.",
    "UNVERIFIED": "Not settled either way. Often the CV does not show the main skill, which is not a rejection.",
    "INELIGIBLE": "A hard blocker was found, such as an experience minimum far above yours.",
    "APPLIED": "Jobs you applied to. Undo moves a job back to Saved and returns it to its filter.",
}
# A job leaves the decision filters once it is past "saved" or "planned".
SUBMITTED = frozenset({"APPLIED", "SCREENING", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN"})
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


def build(snapshot, applications: dict[int, dict], raw: dict) -> dict:
    sources = {s for r in snapshot.rows for s in r["sources"]}
    p = params(raw, sources)
    applied = {jid for jid, a in applications.items() if a["status"] in SUBMITTED}
    open_jobs = [
        r
        for r in snapshot.rows
        if r.get("final_status")
        and r["lifecycle"] == "active"
        and r["listed"]
        and r["job_id"] not in applied
    ]
    counts = {k: sum(r["final_status"] == k for r in open_jobs) for k in TABS}
    counts["APPLIED"] = len([j for j in applied if j in snapshot.by_id])
    if p["d"] == "APPLIED":
        pool = [snapshot.by_id[j] for j in applied if j in snapshot.by_id]
        pool.sort(
            key=lambda r: (applications[r["job_id"]].get("applied_on") or "", r["job_id"]),
            reverse=True,
        )
    else:
        pool = [r for r in open_jobs if r["final_status"] == p["d"]]
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


def _action(item, csrf, next_path, applied_tab) -> str:
    base = (
        f'<input type="hidden" name="csrf" value="{e(csrf)}">'
        f'<input type="hidden" name="next" value="{e(next_path)}">'
    )
    status, label = ("SAVED", "Undo") if applied_tab else ("APPLIED", "Mark applied")
    return (
        f'<form class="apply" method="post" action="/jobs/{item["job_id"]}/status">{base}'
        f'<input type="hidden" name="status" value="{status}">'
        f'<button class="secondary" title="Moves the job {"back to Saved" if applied_tab else "to Applied"}">'
        f"{label}</button></form>"
    )


def _list(title, lines) -> str:
    if not lines:
        return ""
    return f"<div><h4>{e(title)}</h4><ul>{''.join(f'<li>{e(x)}</li>' for x in lines)}</ul></div>"


def _card(item, csrf, next_path, applied_tab) -> str:
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
        f"{_action(item, csrf, next_path, applied_tab)}</article>"
    )


def focus_page(result, csrf) -> str:
    p, counts = result["params"], result["counts"]
    next_path = "/?" + query(p)
    applied_tab = p["d"] == "APPLIED"
    cards = "".join(_card(i, csrf, next_path, applied_tab) for i in result["items"])
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
    return (
        f'<p class="sub">{counts["ELIGIBLE"]} eligible to apply to · {e(HELP[p["d"]])}</p>'
        + _search(result)
        + _tabs(result)
        + cards
        + pager("/", {k: v for k, v in p.items() if k != "page"}, result["page"], result["pages"])
        + more
        + '<p class="note">Eligible, Unverified and Ineligible are suitability for applying (Module 3). '
        "They are never employer-confirmed hiring in Pakistan; that is the separate Module 2 status "
        "on each job.</p>"
    )
