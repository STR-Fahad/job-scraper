"""HTML for the dashboard. Everything user-, job- or database-supplied goes through `e()`."""

import html
from urllib.parse import urlencode

from .. import reviews, tracking
from .data import DIMENSIONS, PAGE_SIZE, SORTS

NAV = (
    ("overview", "/", "Overview"),
    ("jobs", "/jobs", "Job Explorer"),
    ("rankings", "/rankings", "CV Matches & Rankings"),
    ("reviews", "/reviews", "Eligibility Review Queue"),
    ("applications", "/applications", "My Applications"),
)

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2430;--mute:#5b6676;--line:#dfe3ea;--accent:#1f5fbf;
--ok:#1c7a46;--okbg:#e4f4ea;--warn:#8a5a00;--warnbg:#fff2d6;--bad:#a12727;--badbg:#fbe4e4;
--info:#2a5aa8;--infobg:#e6eefb;--neutral:#4a5360;--neutralbg:#eceff3}
@media (prefers-color-scheme:dark){:root{--bg:#14181f;--card:#1c222c;--ink:#e6e9ee;--mute:#9aa5b5;
--line:#2d3644;--accent:#7eb0ff;--ok:#7fd69f;--okbg:#17352a;--warn:#f0c46a;--warnbg:#3a2f12;
--bad:#ff9d9d;--badbg:#3d1d1d;--info:#9fc2ff;--infobg:#1c2c47;--neutral:#c0c8d4;--neutralbg:#2a313c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
header{background:var(--card);border-bottom:1px solid var(--line);padding:0 20px;display:flex;
gap:4px;align-items:center;flex-wrap:wrap}header b{margin-right:16px}
header a{color:var(--mute);text-decoration:none;padding:14px 10px;border-bottom:3px solid transparent}
header a.on{color:var(--ink);border-color:var(--accent)}header a:hover{color:var(--ink)}
main{max-width:1280px;margin:0 auto;padding:20px}h1{font-size:1.5rem;margin:.2rem 0 1rem}
h2{font-size:1.1rem;margin:1.6rem 0 .6rem}h3{font-size:1rem;margin:1rem 0 .4rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin-bottom:14px}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(190px,1fr))}
.stat{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px}
.stat b{font-size:1.6rem;display:block}.stat span{color:var(--mute);font-size:.85rem}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);
border-radius:8px;overflow:hidden}th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);
vertical-align:top;font-size:.9rem}th{background:var(--neutralbg);color:var(--mute);font-weight:600}
tr:last-child td{border-bottom:0}.badge{display:inline-block;padding:1px 8px;border-radius:10px;
font-size:.78rem;font-weight:600;white-space:nowrap}
.b-ok{background:var(--okbg);color:var(--ok)}.b-warn{background:var(--warnbg);color:var(--warn)}
.b-bad{background:var(--badbg);color:var(--bad)}.b-info{background:var(--infobg);color:var(--info)}
.b-neutral{background:var(--neutralbg);color:var(--neutral)}
.mute{color:var(--mute)}.small{font-size:.85rem}a{color:var(--accent)}
.notice{border-radius:8px;padding:10px 14px;margin-bottom:14px}.notice.ok{background:var(--okbg);color:var(--ok)}
.notice.err{background:var(--badbg);color:var(--bad)}.notice.info{background:var(--infobg);color:var(--info)}
.notice.warn{background:var(--warnbg);color:var(--warn)}
form.filters{display:flex;gap:10px;flex-wrap:wrap;align-items:end;margin-bottom:14px}
label{display:flex;flex-direction:column;font-size:.8rem;color:var(--mute);gap:2px}
input,select,textarea,button{font:inherit;padding:6px 8px;border:1px solid var(--line);
border-radius:6px;background:var(--card);color:var(--ink)}button{cursor:pointer;background:var(--accent);
color:#fff;border-color:var(--accent)}button.secondary{background:var(--card);color:var(--ink);border-color:var(--line)}
textarea{width:100%;min-height:80px}.pager{display:flex;gap:10px;align-items:center;margin:12px 0}
pre.desc{white-space:pre-wrap;word-wrap:break-word;background:var(--card);border:1px solid var(--line);
border-radius:8px;padding:12px;max-height:520px;overflow:auto;font:13px/1.5 ui-monospace,Consolas,monospace}
blockquote{margin:.3rem 0;padding:.3rem .8rem;border-left:3px solid var(--line);color:var(--mute);
white-space:pre-wrap}.cols{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(340px,1fr))}
.kv{display:grid;grid-template-columns:160px 1fr;gap:4px 12px}.kv dt{color:var(--mute)}.kv dd{margin:0}
details>summary{cursor:pointer;color:var(--accent)}code{background:var(--neutralbg);padding:1px 5px;border-radius:4px}
@media (max-width:700px){main{padding:12px}.kv{grid-template-columns:1fr}table{display:block;overflow-x:auto}}
"""

REASONS = {
    "listed_on_profile_only": "Listed in the CV skills, but no sentence says what was done with it.",
    "skill_absent_from_approved_profile": "The skill is not in the approved profile (verified gap).",
    "requirement_too_broad_to_verify": "Too general to check against specific CV evidence.",
    "no_supporting_profile_evidence": "No supporting evidence found in the approved profile.",
    "hiring_condition_not_scored": "A hiring condition: recorded, never scored, never decides eligibility.",
    "approved_experience_below_minimum": "The approved experience is below the stated minimum.",
    "technology_years_below_minimum": "Professional years with this technology are below the minimum.",
    "technology_specific_years_not_established": "Years with this specific technology cannot be established.",
    "alternative_requirement_unresolved": "Alternatives were offered; none could be confirmed.",
    "requirement_wording_unresolved": "The wording was ambiguous.",
}
INTERPRETATIONS = {
    "high_score_strong_evidence": "High score with strong evidence.",
    "high_score_limited_evidence": "High score, but part of the evidence is unresolved.",
    "low_score_confirmed_gaps": "Low score driven by gaps confirmed against the approved profile.",
    "low_score_unresolved_evidence": (
        "Low score with mostly unresolved evidence. This is an evidence-limited result, "
        "not proof that the job is a poor fit."
    ),
}
FLAGS = {
    "low_applicable_weight": "Only part of the scoring weight applied (some components were not stated).",
    "few_scored_findings": "Few requirements could be scored.",
    "low_evidence_coverage": "Less than half of the scored findings reached a determined state.",
    "unmet_experience_minimum": "A stated experience minimum exceeds the approved experience.",
    "eligibility_review_pending": "High score, but Pakistan eligibility is not established.",
    "preferences_not_extracted": "Preferred qualifications were stated but not extracted.",
    "manual_review_confirmed_restricted": "A manual review cites evidence that this role is restricted.",
    "manual_review_needs_reverification": "A manual review is out of date and needs reverification.",
}
DIMENSION_LABELS = {
    "category": "Engineering relevance",
    "experience": "Experience",
    "geography": "Geography",
    "restrictions": "Employment restrictions",
    "workplace": "Workplace",
}
CLI_ASSESS = (
    ".venv\\Scripts\\python.exe -m job_scraper match analyze --live --job-id {id} --limit 1"
)


def e(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def human(code) -> str:
    return REASONS.get(code, str(code).replace("_", " ") if code else "")


def badge(kind, text, title=None) -> str:
    tip = f' title="{e(title)}"' if title else ""
    return f'<span class="badge b-{kind}"{tip}>{e(text)}</span>'


def lifecycle_badge(lifecycle, listed) -> str:
    if lifecycle == "active" and listed:
        return badge("ok", "active · listed", "Source still lists this job")
    note = "" if listed else " · unlisted"
    kind = "neutral" if lifecycle in {"closed", "expired"} or not listed else "info"
    return badge(kind, f"{lifecycle}{note}", "State reported by the job source, not by you")


def eligibility_badge(status, stale=False) -> str:
    kind = {"ELIGIBLE": "ok", "INELIGIBLE": "bad", "UNVERIFIED": "warn"}.get(status, "neutral")
    tip = {
        "ELIGIBLE": "Module 2: no blocking rule found; still check the posting",
        "INELIGIBLE": "Module 2: an explicit rule excludes this job",
        "UNVERIFIED": "Module 2: not established either way. This is NOT a rejection.",
    }.get(status, "")
    text = f"{status} (re-screen needed)" if stale else status
    return badge(kind, text, tip)


def app_badge(application) -> str:
    if not application:
        return '<span class="mute">—</span>'
    kind = {
        "SAVED": "neutral",
        "PLANNED": "info",
        "APPLIED": "info",
        "SCREENING": "info",
        "INTERVIEW": "ok",
        "OFFER": "ok",
        "REJECTED": "bad",
        "WITHDRAWN": "neutral",
    }[application["status"]]
    return badge(kind, application["status"], tracking.MEANINGS[application["status"]])


def match_cell(row) -> str:
    """A score is never shown without its confidence, coverage and ceiling."""
    kind = row.get("assessment_status")
    if kind == "current":
        conf = row["confidence"]
        tone = {"high": "ok", "medium": "info", "low": "warn"}.get(conf, "neutral")
        return (
            f"<b>{e(row['score'])}/100</b> {badge(tone, conf + ' confidence')}"
            f'<br><span class="small mute">coverage {e(row["coverage"])}% · ceiling {e(row["ceiling"])}</span>'
        )
    if kind == "stale":
        return badge(
            "warn", "stale assessment", "Not current: the job, profile or rules changed"
        ) + ('<br><span class="small mute">no current score</span>')
    return badge("neutral", "Not assessed", "No assessment exists. This is not a score of 0.")


def tidy_date(value) -> str:
    return e((value or "")[:10]) or "—"


def link_url(url, text=None) -> str:
    safe = tracking.safe_link(url)
    if not safe:
        return '<span class="mute">—</span>'
    return f'<a href="{e(safe)}" target="_blank" rel="noopener noreferrer nofollow">{e(text or "open")}</a>'


def query(params: dict, **changes) -> str:
    merged = {**params, **changes}
    return urlencode({k: v for k, v in merged.items() if v not in (None, "")})


def layout(title, active, body, *, notice=None, error=None, banner=None) -> str:
    nav = "".join(
        f'<a href="{href}"{" class=on" if key == active else ""}>{e(label)}</a>'
        for key, href, label in NAV
    )
    notes = ""
    if banner:
        notes += f'<div class="notice warn">{e(banner)}</div>'
    if notice:
        notes += f'<div class="notice ok">{e(notice)}</div>'
    if error:
        notes += f'<div class="notice err">{e(error)}</div>'
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        "<meta name=referrer content=same-origin>"
        f"<title>{e(title)} · Job Scraper</title><style>{CSS}</style></head><body>"
        f"<header><b>Job Scraper</b>{nav}</header><main>{notes}<h1>{e(title)}</h1>{body}</main>"
        "</body></html>"
    )


def pager(path, params, page, pages) -> str:
    if pages <= 1:
        return ""
    prev = f'<a href="{path}?{query(params, page=page - 1)}">‹ Previous</a>' if page > 1 else ""
    nxt = f'<a href="{path}?{query(params, page=page + 1)}">Next ›</a>' if page < pages else ""
    return f'<div class="pager">{prev}<span class="mute">Page {page} of {pages}</span>{nxt}</div>'


def concept_note() -> str:
    return (
        '<div class="card small mute"><b>Four independent things:</b> the <b>source state</b> '
        "(does the provider still list the job?), <b>Module 2 eligibility</b> (rule-based screening "
        "for a Pakistan-based 0–3-year profile; UNVERIFIED means unknown, not rejected), the "
        "<b>technical match</b> (evidence fit with your approved CV; not eligibility) and "
        "<b>your application status</b>. None of them implies another.</div>"
    )


def stat(value, label) -> str:
    return f'<div class="stat"><b>{e(value)}</b><span>{e(label)}</span></div>'


# --- Overview -------------------------------------------------------------------------


def overview(snapshot, app_counts, profile, orphans) -> str:
    c = snapshot.counts
    elig, active = c["eligibility_all"], c["eligibility_active"]
    by = app_counts["by_status"]
    parts = [concept_note()]
    parts.append("<h2>Collected jobs · source state</h2><div class=grid>")
    parts += [
        stat(c["total"], "Total discovered jobs"),
        stat(c["active_listed"], "Active and listed"),
        stat(c["lifecycle"]["unknown"], "Source state unknown"),
        stat(c["lifecycle"]["closed"] + c["lifecycle"]["expired"], "Closed or expired"),
        stat(c["relevant_active_listed"], "Engineering-relevant, active and listed"),
    ]
    parts.append("</div><h2>Module 2 eligibility · rule-based screening</h2><div class=grid>")
    parts += [
        stat(elig["ELIGIBLE"], f"Eligible (all jobs) · {active['ELIGIBLE']} active"),
        stat(elig["INELIGIBLE"], f"Ineligible (all jobs) · {active['INELIGIBLE']} active"),
        stat(elig["UNVERIFIED"], f"Unverified (all jobs) · {active['UNVERIFIED']} active"),
        stat(c["stale_classifications"], "Screening results out of date"),
    ]
    parts.append("</div><h2>Module 3 · technical match with the approved CV</h2><div class=grid>")
    parts += [
        stat(c["population"], "Matching population"),
        stat(c["assessments_current"], "Current CV assessments"),
        stat(c["assessments_stale"], "Stale CV assessments"),
        stat(c["awaiting_assessment"], "Awaiting assessment"),
    ]
    parts.append("</div><h2>My applications · your status</h2><div class=grid>")
    parts += [
        stat(by["SAVED"], "Saved jobs"),
        stat(by["PLANNED"], "Planned"),
        stat(app_counts["submitted"], "Applications submitted"),
        stat(app_counts["in_progress"], "Applications in progress"),
        stat(by["INTERVIEW"], "Interviews"),
        stat(by["OFFER"], "Offers"),
    ]
    parts.append("</div>")
    if profile:
        months = profile["approved_months"]
        shown = f"{months} months" if months is not None else "not recorded"
        parts.append(
            "<h2>Approved candidate profile (read-only)</h2><div class=card>"
            f"Version {e(profile['version'])} · <code>{e(profile['hash'])}</code> · documented "
            f"professional experience {e(shown)} · {profile['skills']} listed skills, "
            f"{profile['experience_entries']} experience entries, {profile['projects']} projects. "
            "The dashboard never edits the profile.</div>"
        )
    if orphans:
        parts.append(
            '<div class="notice warn">Application records exist for job ids that were merged '
            f"away: {e(', '.join(map(str, orphans)))}. They are kept, not deleted.</div>"
        )
    if c["stale_classifications"]:
        parts.append(
            '<div class="notice info">Some screening results are out of date. Run '
            "<code>python -m job_scraper process</code> (offline) to refresh them.</div>"
        )
    return "".join(parts)


# --- Job Explorer ---------------------------------------------------------------------


def _select(name, label, options, value) -> str:
    opts = "".join(
        f'<option value="{e(k)}"{" selected" if k == value else ""}>{e(v)}</option>'
        for k, v in options
    )
    return f'<label>{e(label)}<select name="{name}">{opts}</select></label>'


def explorer(result, sources) -> str:
    p = result["params"]
    form = (
        '<form class=filters method=get action="/jobs">'
        f'<label>Search title / company<input name=q value="{e(p["q"])}"></label>'
        + _select(
            "relevance",
            "Relevance",
            [("engineering", "Engineering-relevant"), ("", "All jobs")],
            p["relevance"],
        )
        + _select(
            "source", "Source", [("", "All sources"), *[(s, s) for s in sources]], p["source"]
        )
        + _select(
            "listing",
            "Source state",
            [
                ("active_listed", "Active and listed"),
                ("", "All"),
                ("not_active", "Closed / unlisted / unknown"),
            ],
            p["listing"],
        )
        + _select(
            "eligibility",
            "Module 2",
            [
                ("open", "Eligible + Unverified"),
                ("", "All, incl. Ineligible"),
                ("ELIGIBLE", "Eligible"),
                ("UNVERIFIED", "Unverified"),
                ("INELIGIBLE", "Ineligible"),
            ],
            p["eligibility"],
        )
        + _select(
            "workplace",
            "Workplace",
            [
                ("", "Any"),
                ("remote", "Remote"),
                ("hybrid", "Hybrid"),
                ("onsite", "On-site"),
                ("unknown", "Unknown"),
            ],
            p["workplace"],
        )
        + _select(
            "assessment",
            "CV assessment",
            [("", "Any"), ("current", "Current"), ("stale", "Stale"), ("missing", "Not assessed")],
            p["assessment"],
        )
        + _select(
            "tracked",
            "My tracking",
            [
                ("", "Any"),
                ("any", "Tracked"),
                ("none", "Not tracked"),
                *[(s, s.title()) for s in tracking.STATUSES],
            ],
            p["tracked"],
        )
        + _select("sort", "Sort", list(SORTS.items()), p["sort"])
        + "<button>Apply</button> <a href='/jobs?relevance=&listing=&eligibility=&sort=discovered'>"
        "Show all jobs</a></form>"
    )
    rows = []
    for r in result["items"]:
        rows.append(
            "<tr>"
            f'<td><a href="/jobs/{r["job_id"]}"><b>{e(r["title"])}</b></a><br>'
            f'<span class="small mute">{e(r["company"])} · #{r["job_id"]} · {e(", ".join(r["sources"]))}</span></td>'
            f"<td>{lifecycle_badge(r['lifecycle'], r['listed'])}<br>"
            f'<span class="small mute">{e(r["workplace"])}</span></td>'
            f"<td>{eligibility_badge(r['eligibility'], r['classification_stale'])}<br>"
            f'<span class="small mute">experience: {e(r["experience"])}</span></td>'
            f"<td>{match_cell(r)}</td>"
            f"<td>{app_badge(r['application'])}</td>"
            f"<td class=small>{tidy_date(r['first_seen'])}<br>{link_url(r['source_url'], 'posting')}</td></tr>"
        )
    table = (
        "<table><tr><th>Job</th><th>Source state · workplace</th><th>Module 2 · experience</th>"
        "<th>Technical match</th><th>My application</th><th>Discovered · link</th></tr>"
        + "".join(rows)
        + "</table>"
        if rows
        else '<div class="card mute">No jobs match these filters. Try “Show all jobs”.</div>'
    )
    note = ""
    if p["sort"] == "match":
        note = (
            '<div class="notice info">Sorted by technical match: only jobs with a current '
            "assessment are listed, because jobs without one have no score to compare.</div>"
        )
    return (
        concept_note()
        + note
        + form
        + f'<p class="mute small">{result["total"]} job(s) · {PAGE_SIZE} per page</p>'
        + table
        + pager("/jobs", p, result["page"], result["pages"])
    )


# --- Job detail -----------------------------------------------------------------------


def _quote(text, limit=320) -> str:
    text = (text or "").strip()
    return f"<blockquote>{e(text[:limit] + ('…' if len(text) > limit else ''))}</blockquote>"


def module2_panel(classification, row) -> str:
    stale = row and row["classification_stale"]
    if classification is None:
        why = (
            "The stored screening result is out of date for this job (the job or the rules changed). "
            "Run the offline `process` command to refresh it."
            if stale
            else "No screening result is stored for this job."
        )
        return f'<div class="notice warn">{e(why)} Treated as UNVERIFIED.</div>'
    out = [
        '<p class="small mute">UNVERIFIED is not INELIGIBLE: it means the posting does not establish '
        "the answer. Pakistan hiring eligibility is never inferred from remote wording, a worldwide "
        "company or a salary band.</p>",
        "<table><tr><th>Dimension</th><th>Status</th><th>Recorded reasoning and source evidence</th></tr>",
    ]
    for name in DIMENSIONS:
        merged = classification["merged_decisions"].get(name, {})
        cells, seen = [], set()
        for variant in classification.get("variants", []):
            part = variant.get("decisions", {}).get(name, {})
            if part.get("reason") and part["reason"] not in seen:
                seen.add(part["reason"])
                cells.append(f'<div class="small mute">{e(part["reason"])}</div>')
            for ev in part.get("evidence", [])[:4]:
                key = (ev.get("rule"), ev.get("quote"))
                if key in seen:
                    continue
                seen.add(key)
                cells.append(
                    f'<div class="small"><code>{e(ev.get("rule"))}</code></div>{_quote(ev.get("quote"))}'
                )
        out.append(
            f"<tr><td>{e(DIMENSION_LABELS[name])}</td><td>{eligibility_badge(merged.get('status', 'UNVERIFIED'))}"
            f"</td><td>{''.join(cells) or '<span class=mute>No evidence recorded.</span>'}</td></tr>"
        )
    out.append("</table>")
    if classification.get("source_conflict"):
        out.append(
            '<div class="notice warn">Sources for this job disagree; see each variant.</div>'
        )
    return "".join(out)


def _findings(title, items, kind) -> str:
    if not items:
        return ""
    rows = "".join(
        f"<tr><td><code>{e(i.get('requirement_id'))}</code></td><td>{e(i.get('text'))}"
        f"{'<br><span class="small mute">' + e(i.get('job_excerpt')) + '</span>' if i.get('job_excerpt') and i.get('job_excerpt') != i.get('text') else ''}</td>"
        f"<td>{badge(kind, i.get('state', ''))}</td>"
        f"<td class=small>{e(human(i.get('reason')))}"
        f"{'<br>CV evidence: <code>' + e(i.get('profile_evidence_id')) + '</code> (' + e(i.get('support')) + ')' if i.get('profile_evidence_id') else ''}</td></tr>"
        for i in items
    )
    return (
        f"<h3>{e(title)} ({len(items)})</h3><table><tr><th>Item</th><th>Requirement</th>"
        f"<th>State</th><th>Why</th></tr>{rows}</table>"
    )


def assessment_panel(detail, cli_hint) -> str:
    entry, explain = detail["entry"], detail["explanation"]
    history = detail["assessments"]
    out = []
    if entry is None:
        out.append(
            '<div class="card"><b>Not assessed.</b> This job is outside the matching population '
            "(not active/listed, not engineering-relevant, ineligible, or without a description), so no "
            "assessment is expected. This is not a score of 0.</div>"
        )
    elif entry["assessment_status"] != "current":
        stale = entry["assessment_status"] == "stale"
        reasons = sorted({r for h in entry["assessment_history"] for r in h["stale_reasons"]})
        out.append(
            f'<div class="card">{badge("warn" if stale else "neutral", "stale" if stale else "Not assessed")} '
            + (
                "Earlier assessments exist but none is current ("
                + e(", ".join(r.replace("_", " ") for r in reasons))
                + "). Their scores are historical and are not shown as current."
                if stale
                else "No assessment exists yet. This is not a score of 0."
            )
            + f'<br><span class="small mute">Assessing sends the job text to Gemini and is billable; '
            f"the dashboard never does it. Run, after your explicit decision: "
            f"<code>{e(cli_hint)}</code></span></div>"
        )
    else:
        t = explain["technical"]
        out.append(
            '<div class="card">'
            f"<b style='font-size:1.6rem'>{e(t['score'])}/100</b> "
            f"{badge({'high': 'ok', 'medium': 'info', 'low': 'warn'}.get(t['confidence_level'], 'neutral'), t['confidence_level'] + ' confidence')}"
            f"<p>{e(INTERPRETATIONS.get(t['interpretation'], t['interpretation']))}</p>"
            f"<dl class=kv><dt>Evidence coverage</dt><dd>{e(t['evidence_coverage'])}%</dd>"
            f"<dt>Score ceiling</dt><dd>{e(t['score_ceiling'])} (the score if every unverified finding were later confirmed)</dd>"
            f"<dt>Applicable weight</dt><dd>{e(t['applicable_weight'])} of 100</dd>"
            f"<dt>Assessment</dt><dd>#{e(explain.get('assessment_id'))} · {e(explain.get('assessment_mode'))}</dd>"
            f"<dt>Findings</dt><dd>{badge('ok', 'MATCHED ' + str(t['counts']['MATCHED']))} "
            f"{badge('info', 'PARTIAL ' + str(t['counts']['PARTIAL']))} "
            f"{badge('bad', 'UNMATCHED ' + str(t['counts']['UNMATCHED']) + ' (verified gaps)')} "
            f"{badge('warn', 'UNVERIFIED ' + str(t['counts']['UNVERIFIED']) + ' (unresolved)')}</dd></dl>"
            '<p class="small mute">A low-confidence score is evidence-limited. It does not reject the job, '
            "is not proof of poor suitability, and is not an employment-eligibility decision.</p></div>"
        )
        comp = "".join(
            f"<tr><td>{e(name.replace('_', ' '))}</td><td>{e(part['weight'])}</td><td>{e(part['status'].replace('_', ' '))}</td>"
            f"<td>{e(part['points'])}</td><td>{part['matched']}/{part['partial']}/{part['unmatched']}/{part['unverified']}</td></tr>"
            for name, part in t["components"].items()
        )
        out.append(
            "<h3>Components</h3><table><tr><th>Component</th><th>Weight</th><th>Status</th><th>Points</th>"
            f"<th>Matched / partial / unmatched / unverified</th></tr>{comp}</table>"
        )
        for flag in explain.get("flags", []):
            out.append(f'<div class="notice info small">{e(FLAGS.get(flag, flag))}</div>')
        out.append(_findings("Supported (matched or partial)", explain.get("strengths", []), "ok"))
        out.append(
            _findings(
                "Verified gaps against the approved profile", explain["confirmed_gaps"], "bad"
            )
        )
        info = explain["insufficient_information"]
        out.append(
            _findings(
                "Unresolved requirements (not shown to be unmet)",
                info["unresolved_requirements"],
                "warn",
            )
        )
        out.append(_findings("Unresolved duties", info["unresolved_duties"], "warn"))
        out.append(
            _findings("Hiring conditions (never scored)", info["hiring_conditions"], "neutral")
        )
    if history:
        rows = "".join(
            f"<tr><td>#{h['id']}</td><td>{e(h['created_at'][:19])}</td><td>{e(h['model'])}</td>"
            f"<td>{e(h['score'])}</td><td>{'current' if entry and h['id'] == entry.get('assessment_id') else 'historical'}</td></tr>"
            for h in history
        )
        out.append(
            "<h3>Assessment history</h3><p class='small mute'>Kept, never overwritten. Only the one marked "
            f"current counts.</p><table><tr><th>ID</th><th>Created</th><th>Model</th><th>Score</th><th></th></tr>{rows}</table>"
        )
    return "".join(out)


def review_panel(detail, csrf, next_path) -> str:
    history = detail["reviews"]
    rows = "".join(
        f"<tr><td>#{h['id']}</td><td>{e(h['reviewed_at'][:19])}</td>"
        f"<td>{badge('info', h['status'])}<br><span class='small mute'>{e('superseded' if h['superseded'] else h['currency'])}</span></td>"
        f"<td>{e(h['explanation'])}<br>{('<span class=small>Your evidence reference: ' + e(h['evidence']) + '</span>') if h['evidence'] else ''}"
        f"<br><span class='small mute'>{e(h['verification'])}</span></td>"
        f"<td class=small>Module 2 then: {e(h['module2_status_at_review'])}"
        f"{'<br><b>Needs reverification: ' + e(h['currency_reason']) + '</b>' if h['currency'] != 'current' else ''}</td></tr>"
        for h in history
    )
    options = "".join(
        f'<option value="{k}">{e(k)} — {e(v[:90])}</option>' for k, v in reviews.STATUSES.items()
    )
    return (
        '<p class="small mute">Manual reviews are your own notes. They are append-only, are never '
        "treated as independently verified, and never change the Module 2 result or the score.</p>"
        + (
            f"<table><tr><th>ID</th><th>When</th><th>Status</th><th>Explanation · evidence</th><th>Currency</th></tr>{rows}</table>"
            if rows
            else '<div class="card mute">No manual review recorded for this job.</div>'
        )
        + f'<details><summary>Add a manual review</summary><form method=post action="/jobs/{detail["job"]["id"]}/review">'
        f'<input type=hidden name=csrf value="{e(csrf)}"><input type=hidden name=next value="{e(next_path)}">'
        f"<label>Status<select name=status>{options}</select></label>"
        "<label>Explanation (required)<textarea name=explanation maxlength=2000 required></textarea></label>"
        "<label>Evidence reference (URL or quotation; required unless status is unresolved)"
        "<textarea name=evidence maxlength=2000></textarea></label>"
        "<button>Append review</button></form></details>"
    )


def application_panel(job_id, application, history, csrf, next_path) -> str:
    base = f'<input type=hidden name=csrf value="{e(csrf)}"><input type=hidden name=next value="{e(next_path)}">'
    if not application:
        return (
            '<div class="card"><p class=mute>Not tracked. Saving only bookmarks the job; it does not mean you '
            "applied.</p>"
            f'<form method=post action="/jobs/{job_id}/save" style="display:inline">{base}<button>Save job</button></form> '
            f'<form method=post action="/jobs/{job_id}/status" style="display:inline">{base}'
            '<input type=hidden name=status value="PLANNED"><button class=secondary>Plan to apply</button></form></div>'
        )
    a = application
    opts = "".join(
        f'<option value="{s}"{" selected" if s == a["status"] else ""}>{s}</option>'
        for s in tracking.STATUSES
    )
    hist = "".join(
        f"<tr><td>{e(h['changed_at'][:19])}</td><td>{e(h['event'].replace('_', ' '))}</td>"
        f"<td>{e(h['from_status'] or '—')} → {e(h['to_status'])}</td><td class=small>{e(h['summary'])}</td></tr>"
        for h in reversed(history)
    )
    ref = tracking.safe_link(a["reference"])
    return (
        f'<div class="card"><dl class=kv><dt>Status</dt><dd>{app_badge(a)} <span class="small mute">'
        f"{e(tracking.MEANINGS[a['status']])}</span></dd><dt>Saved</dt><dd>{tidy_date(a['saved_at'])}</dd>"
        f"<dt>Date applied</dt><dd>{e(a['applied_on'] or '—')}</dd><dt>Follow-up</dt><dd>{e(a['follow_up_on'] or '—')}</dd>"
        f"<dt>Channel</dt><dd>{e(a['channel'] or '—')}</dd><dt>Reference</dt>"
        f"<dd>{link_url(ref, ref) if ref else e(a['reference'] or '—')}</dd>"
        f"<dt>Last update</dt><dd>{tidy_date(a['updated_at'])}</dd></dl></div>"
        '<div class="cols"><div class="card"><h3>Change status</h3>'
        f'<form method=post action="/jobs/{job_id}/status">{base}<label>Status<select name=status>{opts}</select></label>'
        "<label>Date applied (YYYY-MM-DD; needed for APPLIED and later)"
        f'<input name=applied_on value="{e(a["applied_on"] or "")}" placeholder="YYYY-MM-DD"></label>'
        "<label>Note about this change (kept in history)<input name=note maxlength=200></label>"
        "<button>Update status</button></form></div>"
        '<div class="card"><h3>Details and private notes</h3>'
        f'<form method=post action="/jobs/{job_id}/details">{base}'
        f'<label>Follow-up date<input name=follow_up_on value="{e(a["follow_up_on"] or "")}" placeholder="YYYY-MM-DD"></label>'
        f'<label>Application channel<input name=channel maxlength=100 value="{e(a["channel"] or "")}"></label>'
        f'<label>Reference or link<input name=reference maxlength=500 value="{e(a["reference"] or "")}"></label>'
        f"<label>Private notes (stored locally only)<textarea name=notes maxlength=5000>{e(a['notes'] or '')}</textarea></label>"
        "<button>Save details</button></form></div></div>"
        f"<h3>Application history</h3><table><tr><th>When (UTC)</th><th>Event</th><th>Change</th><th>Note</th></tr>{hist}</table>"
    )


def sources_panel(detail) -> str:
    rows = "".join(
        f"<tr><td>{e(s['source'])}{(' / ' + e(s['board'])) if s['board'] else ''}</td>"
        f"<td>{lifecycle_badge(s['lifecycle_state'], s['is_listed'] == 1)}</td>"
        f"<td class=small>first seen {tidy_date(s['first_seen'])}<br>last seen {tidy_date(s['last_seen'])}"
        f"<br>checked {tidy_date(s['last_checked_at'])}</td>"
        f"<td>{link_url(s['source_url'], 'source page')}<br>{link_url(s['application_url'], 'apply link')}</td></tr>"
        for s in detail["sources"]
    )
    return f"<table><tr><th>Source</th><th>Source state</th><th>Observed</th><th>Links</th></tr>{rows}</table>"


def job_page(detail, csrf, next_path) -> str:
    job, row = detail["job"], detail["row"]
    description = job["description"] or ""
    shown = description[:30000]
    cut = (
        f'<p class="small mute">Showing the first 30,000 of {len(description):,} characters.</p>'
        if len(description) > 30000
        else ""
    )
    head = (
        f"<p><b>{e(job['company'])}</b> · #{job['id']} · {lifecycle_badge(job['status'], job['is_listed'] == 1)} "
        f"{eligibility_badge(row['eligibility'], row['classification_stale']) if row else ''} "
        f"{app_badge(detail['application'])}</p>"
        f"<p>{link_url(job['source_url'], 'Open original posting')} · {link_url(job['application_url'], 'Open apply link')}"
        f' · <span class="small mute">links open the employer or provider site; the dashboard never applies for you</span></p>'
    )
    info = (
        "<dl class=kv>"
        f"<dt>Workplace</dt><dd>{e(job['workplace_type'] or 'unknown')}</dd>"
        f"<dt>Location</dt><dd>{e(job['location'])}</dd>"
        f"<dt>Employment type</dt><dd>{e(job['employment_type'])}</dd>"
        f"<dt>Posted</dt><dd>{tidy_date(job['posted_at'])}</dd><dt>Updated by source</dt><dd>{tidy_date(job['updated_at'])}</dd>"
        f"<dt>Discovered</dt><dd>{tidy_date(job['first_seen'])}</dd><dt>Last seen</dt><dd>{tidy_date(job['last_seen'])}</dd></dl>"
    )
    return (
        head
        + concept_note()
        + "<h2>My application</h2>"
        + application_panel(
            job["id"], detail["application"], detail["application_history"], csrf, next_path
        )
        + "<h2>Module 2 screening</h2>"
        + module2_panel(detail["classification"], row)
        + "<h2>Module 3 technical match</h2>"
        + assessment_panel(detail, CLI_ASSESS.format(id=job["id"]))
        + "<h2>Manual eligibility reviews</h2>"
        + review_panel(detail, csrf, next_path)
        + "<h2>Job information</h2>"
        + info
        + "<h2>Source observations</h2>"
        + sources_panel(detail)
        + "<h2>Description</h2>"
        + cut
        + f'<pre class="desc">{e(shown)}</pre>'
    )


# --- Rankings --------------------------------------------------------------------------


def rankings(ranked, queue, params, profile) -> str:
    counts = ranked["counts"]
    months = (ranked.get("approved_experience") or {}).get("months")
    intro = (
        '<div class="notice info"><b>This is a ranking among assessed jobs only</b> '
        f"({counts['current']} of {counts['population']} jobs in the matching population have a current "
        "assessment). A job ranked #1 may simply be the only one assessed. Scores are technical evidence "
        "fit with the approved CV, not hiring probability and not Pakistan eligibility.</div>"
        + (
            f'<p class="small mute">Approved professional experience: {e(months)} months.</p>'
            if months is not None
            else ""
        )
    )
    filters = (
        '<form class=filters method=get action="/rankings">'
        + _select(
            "eligibility",
            "Module 2",
            [("", "Any"), ("ELIGIBLE", "Eligible"), ("UNVERIFIED", "Unverified")],
            params.get("eligibility", ""),
        )
        + _select(
            "confidence",
            "Confidence",
            [("", "Any"), ("high", "High"), ("medium", "Medium"), ("low", "Low")],
            params.get("confidence", ""),
        )
        + "<button>Filter</button></form>"
    )
    rows = "".join(
        f"<tr><td>{e(r.get('rank'))}</td><td><a href='/jobs/{r['job_id']}'><b>{e(r['title'])}</b></a><br>"
        f"<span class='small mute'>{e(r['company'])} · #{r['job_id']}</span></td>"
        f"<td>{match_cell({'assessment_status': 'current', 'score': r['technical']['score'], 'confidence': r['technical']['confidence_level'], 'coverage': r['technical']['evidence_coverage'], 'ceiling': r['technical']['score_ceiling']})}</td>"
        f"<td>{eligibility_badge(r['module2']['status'])}</td>"
        f"<td class=small>M {r['technical']['counts']['MATCHED']} · P {r['technical']['counts']['PARTIAL']} · "
        f"U {r['technical']['counts']['UNMATCHED']} · V {r['technical']['counts']['UNVERIFIED']}</td>"
        f"<td class=small>{e(INTERPRETATIONS.get(r['technical']['interpretation'], ''))}"
        + "".join(f"<br>{e(FLAGS.get(f, f))}" for f in r.get("flags", [])[:3])
        + "</td></tr>"
        for r in ranked["ranked"]
    )
    table = (
        "<table><tr><th>#</th><th>Job</th><th>Technical match</th><th>Module 2</th>"
        "<th>M / P / unmatched / unverified</th><th>Explanation</th></tr>" + rows + "</table>"
        if rows
        else f'<div class="card mute">{e(ranked["empty_state"] or "No ranked job matches.")}</div>'
    )
    waiting = "".join(
        f"<tr><td><a href='/jobs/{a['job_id']}'>{e(a['title'])}</a><br><span class='small mute'>{e(a['company'])} · #{a['job_id']}</span></td>"
        f"<td>{eligibility_badge(a['module2']['status'])}</td>"
        f"<td>{badge('warn' if a['assessment_status'] == 'stale' else 'neutral', 'stale' if a['assessment_status'] == 'stale' else 'Not assessed')}</td></tr>"
        for a in queue["awaiting"][:50]
    )
    awaiting = (
        f"<h2>Awaiting assessment ({queue['count']})</h2>"
        '<p class="small mute">No current assessment, so no score (never 0). The dashboard does not call Gemini. '
        "After your explicit decision, assess one job with "
        "<code>python -m job_scraper match analyze --live --job-id ID --limit 1</code> (billable).</p>"
        + (
            f"<table><tr><th>Job</th><th>Module 2</th><th>Assessment</th></tr>{waiting}</table>"
            + (
                f"<p class='small mute'>Showing the first 50 of {queue['count']}.</p>"
                if queue["count"] > 50
                else ""
            )
            if waiting
            else '<div class="card mute">Nothing is waiting.</div>'
        )
    )
    held = ranked["restricted_not_recommended"]
    held_html = ""
    if held:
        held_html = (
            "<h2>Manually restricted (not recommended)</h2><table>"
            + "".join(
                f"<tr><td><a href='/jobs/{r['job_id']}'>{e(r['title'])}</a> · {e(r['company'])}</td><td>{e(r['technical']['score'])}/100</td></tr>"
                for r in held
            )
            + "</table>"
        )
    return intro + filters + "<h2>Ranked assessed jobs</h2>" + table + held_html + awaiting


# --- Review queue ---------------------------------------------------------------------


def review_queue_page(result) -> str:
    f = result["filters"]
    form = (
        '<form class=filters method=get action="/reviews">'
        + _select(
            "dimension",
            "Unresolved dimension",
            [
                ("", "Any"),
                ("geography", "Geography"),
                ("restrictions", "Employment restrictions"),
                ("experience", "Experience"),
                ("workplace", "Workplace"),
            ],
            f["dimension"],
        )
        + _select(
            "review",
            "Manual review",
            [
                ("", "Any"),
                ("none", "Never reviewed"),
                ("reviewed", "Reviewed"),
                ("needs_reverification", "Needs reverification"),
            ],
            f["review"],
        )
        + "<button>Filter</button></form>"
    )
    rows = []
    for i in result["items"]:
        evidence = "".join(
            f"<div class=small><b>{e(DIMENSION_LABELS[name])}</b>"
            + "".join(
                (
                    _quote(q["quote"], 200)
                    if q.get("quote")
                    else f"<div class='small mute'>{e(q.get('reason') or 'No source evidence recorded: the posting does not say.')}</div>"
                )
                for q in quotes
            )
            + "</div>"
            for name, quotes in i["evidence"].items()
        )
        review = i["review"]
        status = (
            f"{badge('info', review['status'])} {e(reviews.label(review))}"
            + f"<br><span class='small mute'>{e(review['verification'])}</span>"
            if review
            else '<span class="mute">never reviewed</span>'
        )
        rows.append(
            f"<tr><td><a href='/jobs/{i['job_id']}'><b>{e(i['title'])}</b></a><br><span class='small mute'>{e(i['company'])} · #{i['job_id']}</span></td>"
            f"<td>{eligibility_badge('UNVERIFIED')}<br><span class='small'>{e(', '.join(DIMENSION_LABELS[d] for d in i['unresolved']))}</span></td>"
            f"<td>{evidence}</td><td>{status}</td></tr>"
        )
    table = (
        "<table><tr><th>Job</th><th>Module 2 · unresolved</th><th>Source evidence</th><th>Latest manual review</th></tr>"
        + "".join(rows)
        + "</table>"
        if rows
        else '<div class="card mute">No job needs review under these filters.</div>'
    )
    intro = (
        '<div class="card small mute">Jobs whose automated screening could not establish eligibility. '
        "<b>System evidence</b> is quoted from the posting; a <b>manual review</b> is your own claim and is "
        "never shown as independently verified. Open a job to append a review; earlier reviews are kept.</div>"
    )
    return (
        intro
        + form
        + f'<p class="small mute">{result["total"]} job(s)</p>'
        + table
        + pager("/reviews", {k: v for k, v in f.items()}, result["page"], result["pages"])
    )


# --- My Applications -------------------------------------------------------------------


def applications_page(listing, counts, params, page, pages) -> str:
    chips = " ".join(
        f'<a href="/applications?{query({}, status=s)}">{badge("neutral", f"{s} {n}")}</a>'
        for s, n in counts["by_status"].items()
    )
    summary = (
        f'<p class="small">{counts["total"]} tracked · {counts["submitted"]} submitted · '
        f'{counts["in_progress"]} in progress</p><p>{chips} <a href="/applications">all</a></p>'
    )
    form = (
        '<form class=filters method=get action="/applications">'
        f'<label>Search job / company<input name=q value="{e(params.get("q", ""))}"></label>'
        + _select(
            "status",
            "Status",
            [("", "All"), *[(s, s) for s in tracking.STATUSES]],
            params.get("status", ""),
        )
        + "<button>Filter</button></form>"
    )
    rows = "".join(
        f"<tr><td><a href='/jobs/{a['job_id']}'><b>{e(a['title'] or 'Job #' + str(a['job_id']) + ' (record merged or missing)')}</b></a><br>"
        f"<span class='small mute'>{e(a['company'] or '')} · #{a['job_id']}</span></td>"
        f"<td>{app_badge(a)}</td>"
        f"<td>{lifecycle_badge(a['job_status'], a['is_listed'] == 1) if a['job_status'] else '<span class=mute>unknown</span>'}"
        f"<br><span class='small mute'>source state, independent of your status</span></td>"
        f"<td class=small>applied {e(a['applied_on'] or '—')}<br>follow-up <b>{e(a['follow_up_on'] or '—')}</b></td>"
        f"<td class=small>{e((a['notes'] or '')[:140])}{'…' if a['notes'] and len(a['notes']) > 140 else ''}</td>"
        f"<td><a href='/jobs/{a['job_id']}'>manage</a></td></tr>"
        for a in listing["items"]
    )
    if rows:
        table = (
            "<table><tr><th>Job</th><th>My status</th><th>Source job state</th><th>Dates</th>"
            f"<th>Notes</th><th></th></tr>{rows}</table>"
        )
    elif counts["total"] == 0:
        table = (
            '<div class="card">No applications yet. Find a job in the <a href="/jobs">Job Explorer</a>, '
            "open it and choose “Save job” or “Plan to apply”.</div>"
        )
    else:
        table = '<div class="card mute">No application matches these filters.</div>'
    return summary + form + table + pager("/applications", params, page, pages)
