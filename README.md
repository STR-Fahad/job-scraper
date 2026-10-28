# Personal Job Collection Module

## Gemini API connection

The optional `gemini-check` command uses the official `google-genai` SDK and sends
one synthetic prompt. It does not read stored jobs, run matching, or alter the
collection and eligibility modules. The default model is `gemini-3.5-flash-lite`;
set `GEMINI_MODEL` or pass `--model` to change it.

Create a key at [Google AI Studio](https://aistudio.google.com/app/apikey). In a
local PowerShell terminal, run `./scripts/setup_gemini_key.ps1` and paste the key
at its hidden prompt. Do not put the key in chat, source files, or a Git commit.
The script stores it in your Windows user environment; new terminals inherit it.
Then test with:

```powershell
Set-Location 'C:\job scraper'
.\.venv\Scripts\python.exe -m job_scraper gemini-check
.\.venv\Scripts\python.exe -m job_scraper gemini-check --model gemini-3.8-flash
```

The command prints only a status and model name. API failures also report the HTTP
status and a safe category. It never prints API error messages, headers, or bodies.
The probe uses the SDK's `v1beta` Gemini Developer API endpoint and allows up to
120 seconds per request. A `temporary_server` result (HTTP 5xx) can be retried
later; the SDK handles transient retries automatically.

## Module 3B.1: local CV profile and explainable matching

Import a text-based PDF CV only by an explicit path. The original PDF stays local;
PDFs, `data/`, and `profiles/` are Git-ignored. Extraction stores only a structured
professional draft. Contact lines, email addresses, phone numbers, URLs, and
recognizable street addresses are removed. Review the result carefully: automated
redaction cannot reliably recognize every form of personal information. Scanned
PDFs need local OCR first. No experience years or work authorization are inferred.

```powershell
Set-Location 'C:\job scraper'
.\.venv\Scripts\python.exe -m job_scraper profile import --file 'C:\path\to\your-cv.pdf'
.\.venv\Scripts\python.exe -m job_scraper profile preview
.\.venv\Scripts\python.exe -m job_scraper profile export
# Edit data/candidate-profile-review.json locally; remove or correct anything inaccurate.
.\.venv\Scripts\python.exe -m job_scraper profile approve --file data/candidate-profile-review.json
.\.venv\Scripts\python.exe -m job_scraper match candidates --limit 20
.\.venv\Scripts\python.exe -m job_scraper match analyze      # Offline preview: no API request
.\.venv\Scripts\python.exe -m job_scraper match analyze --live --limit 3
.\.venv\Scripts\python.exe -m job_scraper match results
```

`--live` is required for billable Gemini calls. The default maximum is three
requests per run and the hard maximum is five. Set `[matching]` in `config.toml`
for `max_requests_per_run` (1–5), `retries` (0–3), and `timeout_seconds` (10–300).
`GEMINI_MODEL` or `match analyze --model` selects the model; the default is
`gemini-3.5-flash-lite`. A successful assessment is cached by job content,
approved profile hash, model, screening version, prompt/schema version, and
scoring weights. Failed calls are not cached. API rate limits stop the batch.

The default queue includes only active, listed, relevant jobs that Module 2 marks
ELIGIBLE or UNVERIFIED. It also checks engineering titles and descriptions when
Module 2 could not establish a category. Use `--include-history` for explicitly
labeled closed, expired, or unknown-lifecycle analysis; those jobs are not treated
as active. An AI match never changes Module 2 eligibility.

Gemini receives job-side evidence from at most 12,000 characters of each selected
job description; longer descriptions are rejected before an API request so no
requirements are silently truncated. It does not receive the PDF, approved profile, contact data,
candidate skill catalog, raw provider payloads, or all jobs automatically. Python
first preserves each logical source line or bullet with a stable R-number, section,
source offset, and explicit technology inventory. Gemini classifies every supplied ID
without generating skill names. Python rejects missing, invented, duplicate, and inconsistent IDs,
then retrieves source text locally and selects CV support from the approved skill catalog,
derives MATCHED, PARTIAL, UNMATCHED or UNVERIFIED, and compares numeric experience
minimums with the approved total. Professional evidence can establish a direct
match. A required skill supported only by a project earns PARTIAL; a preferred
project skill may earn MATCHED. Ambiguous or listed-only evidence earns no
confirmed match. A duty with multiple shared technical terms and hands-on CV
evidence can earn transferable PARTIAL credit. Technologies are equivalent only
through explicit aliases. Alternative requirements such as "at least one of"
remain UNVERIFIED until their group meaning can be established safely.
The 40/25/20/15 component weights are configurable. PARTIAL earns half of its
component share; UNMATCHED and UNVERIFIED earn zero and remain distinguishable
in the breakdown. A complete extraction can therefore earn zero when the
candidate lacks or cannot verify the required qualifications. Missing skill
requirements, duties, explicit numeric experience requirements, or unclassified
source items reject the response. Absence of optional or
unspecified categories does not invalidate an extraction. Inspect cited source
text for nuanced qualifications.
The requirement, response, prompt, evidence and scoring versions are included in the cache
key, so earlier assessments become stale when these rules change.

Python 3.13, HTTPX, BeautifulSoup, Pydantic and SQLite. Six public-API collectors
share an interface, validated records, host pacing, transactional persistence and
lifecycle tracking. The matching module is offline by default; there is no
dashboard or automatic application submission.

## Run on Windows

```powershell
Set-Location 'C:\job scraper'
# Fresh setup only (the local .venv is already prepared):
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[test]'

.\.venv\Scripts\python.exe -m job_scraper collect --all
.\.venv\Scripts\python.exe -m job_scraper collect --source arbeitnow --max-pages 2 --json
.\.venv\Scripts\python.exe -m job_scraper collect --source greenhouse --board enveritas
.\.venv\Scripts\python.exe -m job_scraper jobs
.\.venv\Scripts\python.exe -m job_scraper jobs --include-history
```

`job-scraper` is also installed in `.venv\Scripts`. Activation is optional.
Default database: `C:\job scraper\data\jobs.sqlite3`. No automatic scheduling or
GitHub upload occurs. Logs go to stderr; results/statistics go to stdout.

## Registry and configuration

`config.toml` retains the three aggregate feeds and disabled, verified examples
for Enveritas (Greenhouse), Ashby, and Spreetail (Lever). These are examples, not a
chosen employer watchlist. Existing manual configuration remains authoritative.
Copy to `config.local.toml` for personal changes; pass `--config` to any command.
Relative database paths resolve against the configuration file.

```powershell
.\.venv\Scripts\python.exe -m job_scraper sources list
.\.venv\Scripts\python.exe -m job_scraper sources enable --source greenhouse --board enveritas
.\.venv\Scripts\python.exe -m job_scraper sources disable --source greenhouse --board enveritas
```

Enable/disable commands write a database overlay, not the TOML file. Board identity
includes the provider and region. Lever uses `--region eu` for EU boards. Provider
names, board tokens and configuration keys are validated. No global ATS employer
search endpoint is assumed.

Manual employer entry:

```toml
[[sources]]
source = "greenhouse"
board = "enveritas"
company = "Enveritas"
enabled = true
fetch_details = true
interval_seconds = 2
empty_confirmation_seconds = 3600
```

Global controls: `timeout_seconds=30`, `retries=3` additional attempts,
`max_retry_delay=120`. Source controls: `max_pages=100`, `page_size=20`,
`interval_seconds=2`, `cooldown_seconds`, `fetch_details` (Greenhouse only), and
`empty_confirmation_seconds=3600`. `--max-pages` overrides list-page limits only;
it does not limit a full-board response or Greenhouse detail requests.

Himalayas has a minimum daily scan cooldown and Remotive a six-hour cooldown and
31-second request interval. Failed and capped attempts also consume these scan
cooldowns. Choose limits before collecting. Repeated validation must not erase
cooldown records to force extra traffic.

## Controlled company discovery

Discover board identifiers from a supplied public ATS link, or from links/iframes
on one explicitly permitted company careers page. This is bounded link discovery,
not an internet-wide company search or identifier guessing service.

```powershell
# Validate a known public careers/ATS URL; retain for approval:
.\.venv\Scripts\python.exe -m job_scraper discover --company Enveritas --url https://job-boards.greenhouse.io/enveritas
.\.venv\Scripts\python.exe -m job_scraper sources approve --source greenhouse --board enveritas
.\.venv\Scripts\python.exe -m job_scraper sources enable --source greenhouse --board enveritas

# Only for a company page you are permitted to fetch:
.\.venv\Scripts\python.exe -m job_scraper discover --company 'Company Name' --url https://company.example/careers --allow-careers-fetch
```

The second example needs a real permitted URL. The explicit flag confirms the
page's permitted use; robots.txt must also allow access. Missing/unavailable
robots.txt conservatively stops page discovery. Private/reserved network addresses
are blocked. Redirects and access denials stop requests. Only the supplied page is
read, with at most 20 unique ATS candidates verified. No recursive crawling,
search-engine scraping, CAPTCHA solving or login bypass is performed.

Each candidate is checked against its official public postings endpoint. Discovery
URL, board URL, verification time and evidence are saved; repeated discoveries do
not duplicate the board. `--approve-verified` can approve nonempty successfully
verified boards, but they still start disabled. Empty boards require explicit
approval. Failed verification stays pending and cannot be approved/enabled until
re-verification succeeds. Endpoint verification proves availability, not legal
ownership; the supplied careers link and company label are provenance evidence.
JavaScript-only career pages without HTML links require a direct ATS URL.

## API behavior

Official contracts reviewed October 25, 2026:

| Source | Documented GET endpoint | Paging |
| --- | --- | --- |
| [Himalayas](https://himalayas.app/docs/remote-jobs-api) | `https://himalayas.app/jobs/api` | Opaque `nextCursor`, `limit` <= 20 |
| [Greenhouse](https://docs.greenhouse.io/job-board.html) | `https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true` | Complete board; detail `/jobs/{id}?pay_transparency=true` |
| [Ashby](https://developers.ashbyhq.com/docs/public-job-posting-api) | `https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true` | Complete board |
| [Lever](https://github.com/lever/postings-api) | `https://api.lever.co/v0/postings/{site}?mode=json` | `skip`/`limit`; EU host `api.eu.lever.co` |
| [Remotive](https://github.com/remotive-com/remote-jobs-api) | `https://remotive.com/api/remote-jobs` | Complete available public feed |
| [Arbeitnow](https://www.arbeitnow.com/blog/job-board-api) | `https://www.arbeitnow.com/api/job-board-api` | `links.next`, same HTTPS endpoint only |

No unsupported pagination is added. Caps report `truncated=true`. Repeated
pages/cursors fail safely. Lever includes lists, closing and salary descriptions.
Greenhouse requests complete details; a failed detail request stops remaining
board enrichment, retains list records, and prevents that scan from closing jobs.

## Normalized records

`Job` includes source identity, company, title, descriptions, locations,
restrictions, employment types, salary, dates, URLs, attribution, raw JSON and
response metadata. Source values are retained unchanged in `raw`.

`workplace_type` is `remote`, `hybrid`, `onsite`, or `unknown`. Explicit Ashby/Lever
workplace enums map consistently. Himalayas/Remotive are remote feeds. Arbeitnow
`remote=true` maps to remote; false is unknown because it cannot distinguish hybrid
from onsite. Greenhouse free-text locations are not guessed into workplace types.
Country eligibility remains in restrictions, independent of workplace arrangement.
The deprecated read-only `remote` compatibility accessor returns true for remote,
false for onsite, and null for hybrid/unknown. It is not serialized in new records.

Greenhouse detail `first_published` maps to UTC `posted_at`; `updated_at` remains
separate. Original timestamp strings stay in `raw`. Ashby publication dates and
Arbeitnow/Remotive dates are normalized; unzoned dates assume UTC. Himalayas
`pubDate` is an update timestamp. Lever `createdAt` remains raw: its public API
contract does not establish it as a publication date. Greenhouse application
 deadlines remain raw and never establish definite closure.

Ashby `isListed=false` is stored as `is_listed=false` for direct-link/history use.
It is excluded from ordinary discovery listings. Always use the `jobs` command,
`Store.list_jobs()`, or `discovery_jobs` SQL view for normal discovery; raw tables
and `--include-history` intentionally include inactive and unlisted records.

Salary fields: `minimum`, `maximum`, `currency`, `period`, `compensation_type`,
`components`, `text`, `details`. Ashby base-salary summary components populate the
common numeric fields. Equity and bonuses remain distinct components. Multiple
ambiguous bases leave top-level amounts null. Greenhouse cents convert to currency
units; its unspecified compensation period remains null. Multiple pay ranges stay
as separate components. OTE-labelled ranges are not asserted to be base salary.
Text-only salary is retained without speculative parsing, annualization or currency
conversion. Missing facts remain null/empty, not invented.

## Lifecycle and historical data

Lifecycle is tracked **per source posting**, then aggregated per logical job:
`active`, `closed`, `expired`, `unknown`. Migration starts legacy sightings as
unknown until checked; old timestamps alone do not prove current activity.

- Successful observations mark that source active (or expired if explicit expiry
  has passed). Unlisted is a separate visibility property, not closure.
- A successful, exhaustive, nonempty scan closes previously known postings absent
  from that same source/board. The evidence records scan ID and check time.
- Failed, interrupted, capped, invalid-record, skipped or otherwise partial scans
  never close missing jobs. Other boards/providers are unaffected.
- An empty feed requires two consecutive successful exhaustive empty scans,
  separated by `empty_confirmation_seconds`, before closing prior postings.
  A failed/interrupted scan breaks that confirmation chain.
- Explicit Himalayas expiry timestamps mark expiry. Application deadlines are not
  treated as definite closure. Expiry is evaluated on database opening and listing;
  ordinary discovery also excludes past-expiry representatives at query time.
- Reappearing postings become active. Historical rows and lifecycle transition
  evidence remain stored. A second active provider keeps a duplicate logical job
  active even if one provider closes its copy.

`closed` means absent from the source's exhaustive feed, not independent proof that
an employer has stopped accepting applications. The source may remove postings for
other reasons. `first_seen_at`, `last_seen_at`, `last_checked_at` are exposed through
`job_details`; per-source timestamps and evidence are in `job_sources`, and
append-only transitions are in `lifecycle_events`. Expiry evaluation does not
pretend to be a new network check.

## Storage, migration and single-writer safety

Schema version 2 preserves `jobs`, `job_sources`, URL aliases and prior statistics.
Each existing v1 database is backed up with SQLite's backup API before transactional
DDL, then re-normalized from retained source payloads. Malformed legacy payloads
are preserved, not dropped. Backups go beside the database under `backups/`.
Unknown newer schemas are rejected. You can migrate explicitly:

```powershell
.\.venv\Scripts\python.exe -m job_scraper migrate
```

The original database also has a separate pre-hardening backup in `data/backups`.
Keep backups until you are satisfied. SQLite integrity and foreign-key checks are
part of validation. WAL and busy timeouts remain enabled.

All `Store` users acquire an OS-level lock next to the database. A concurrent writer
fails clearly instead of overlapping scans or migrations. Locks are automatically
released when a process exits or crashes; the small `.lock` file may remain and is
not a stale lock. Coordination is per database, not across separate installations
or separate database paths. Read-only consumers can open SQLite independently.

Deduplication still uses scoped source IDs, conservative canonical URLs and known
job-specific ATS application aliases. Generic application pages do not merge jobs.
Transactions preserve every source record when logical duplicates merge. Original
URLs remain unchanged for attribution/application use. No fuzzy matching or
redirect-resolution deduplication is included. The representative favors active,
listed source records; lifecycle does not erase historical metadata.

## HTTP and responsible use

Timeouts and bounded retries handle transport errors and HTTP 408/429/5xx responses.
Supported Retry-After seconds and HTTP dates set a shared host deadline **before**
retry exhaustion checks. Other boards on that host obey it. Deadlines persist in
SQLite across process runs. Delays above 30 seconds or the configured budget defer
that host without a long blocking sleep. Other hosts may continue. Smaller waits
and normal pacing are injectable for tests.

Preserve attribution in any future display: Himalayas Apply links must remain the
supplied `applicationLink`; show Himalayas credit. Remotive requires its source
credit and listing link, and its public data is delayed. Do not redistribute those
feeds to third-party aggregators. Retain Arbeitnow's backlink. Review provider terms
before changing this personal archive into a public product. Original HTML is
untrusted: sanitize before any future browser display. Keys belong only in the
`HIMALAYAS_API_KEY` environment variable, not source control or URLs.

## Statistics and tests

Collection prints inserted, updated, unchanged, invalid/rejected, duplicates,
merged, closed, expired, hidden, errors, skipped, truncated and exhaustive values.
Counts are per source observation/transition; duplicate and visibility counters
overlap. `unchanged` describes content, not last-seen timestamps. Empty-feed
confirmation is reported separately. An interrupted scan remains recorded as
interrupted and cannot produce disappearance closures.

Exit codes: 0 success or cooldown skip (inspect truncation), 1 source/record errors,
2 configuration/storage/lock errors, 130 interruption. No automatic retry worker is
installed: deferred hosts are retried on a later permitted invocation.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
```

Tests use mocked APIs and injectable clocks, with scratch files in `work/pytest`.
Coverage includes all six collectors, normalization, pagination, retry recovery,
host cooldown persistence, source failures, lifecycle transitions and partial-scan
safety, company discovery, old-data migration, deduplication and process locking.
Live validation evidence is retained separately under `work/hardening`.
# Module 2: processing and eligibility

Run from `C:\job scraper` after collecting jobs:

```powershell
.\.venv\Scripts\python.exe -m job_scraper process
.\.venv\Scripts\python.exe -m job_scraper results --status UNVERIFIED
.\.venv\Scripts\python.exe -m job_scraper results --status ELIGIBLE --category Python
.\.venv\Scripts\python.exe -m job_scraper results --job-id 1 --include-history
```

Both commands accept `--config path\to\config.toml` and emit JSON. `process`
classifies every stored logical job, reports status counts, and skips unchanged
inputs. `results` filters by status, category, or job ID and includes exact source
quotes, field names, rules, processing timestamp, and source-specific decisions.
Categories: backend, full-stack, Rails, Python, AI/ML, LLM, data engineering, DevOps.
Use quotes for categories with spaces. Multiple categories can apply.

The fixed screening profile is **working from Pakistan with a target experience
range of 0–3 years**. This is a range-based shortlist, not proof that a particular
applicant has the required experience. Citizenship, residency rights, foreign work
authorization, sponsorship needs, and working-hour availability remain unknown.
No external requests, LLMs, matching, or dashboard are involved.

* **ELIGIBLE:** all screening dimensions have positive evidence, including
  resolved employment and authorization conditions. This is
  an assessment of the posting, not independent verification of hiring legality.
* **INELIGIBLE:** a supported requirement excludes this profile, such as a
  required minimum above three years or a clearly restricted foreign territory.
* **UNVERIFIED:** missing information, ambiguous language, unresolved employment
  restrictions, stale results, or conflicting source classifications. These jobs
  are retained and included unless explicitly filtered out.

Remote does **not** mean Pakistan-eligible. The workplace screen accepts fully
remote jobs, rejects hybrid and onsite jobs even when their office is in Pakistan,
and leaves unknown arrangements UNVERIFIED. Explicit mandatory office attendance
in a description rejects a job even if an API labels it remote; optional office
visits do not. Candidate-territory fields or direct applicant-facing statements
can establish geography; office locations alone cannot establish remote hiring
eligibility. Time-zone constraints require review. Empty country
lists do not imply worldwide hiring. “No sponsorship” does not prove exclusion:
the candidate's sponsorship needs are unknown.

A definite incompatible requirement takes precedence over unrelated unknown
requirements (for example US-only hiring plus unknown schedule availability).
Genuinely contradictory permission/exclusion claims about the same territory
remain UNVERIFIED. “Anywhere in the EU” means EU-only, not worldwide. Country
names and codes use exact ISO lookups from [pycountry](https://github.com/pycountry/pycountry);
invalid codes and ambiguous business regions remain unknown. No fuzzy geocoding
or assumption from a city name is used.

Missing citizenship or work authorization remains UNVERIFIED. Explicit permitted
Pakistan engagement, citizenship neutrality, and a waiver of foreign authorization
can together resolve the employment dimension; no special phrase is required.
Any additional sponsorship, residency, clearance or contractual restriction is
evaluated independently. A geographic invitation alone is insufficient.

Rules are intentionally conservative and English-focused. They recognize numeric
experience ranges and months, avoid preferred-only requirements and common company
history statements, and keep unfamiliar wording unverified. Minimums, upper bounds,
optional sections, alternative skills and degree-based experience credits are
distinguished. Degree credits remain UNVERIFIED when the applicant's degree is
unknown. Category evidence must describe relevant engineering work; incidental
and negated technologies in sales/mechanical roles cannot establish relevance.
Category keywords require an appropriate engineering/developer title;
generic or unfamiliar titles remain unverified. These rules do not comprehend
every possible sentence or country/region name. Human review remains necessary.

Classifications live in the additive `job_classifications` table, separate from
job/source payloads. Its creation first makes an integrity-checked SQLite backup
in the database's `backups` folder. It leaves collection schema version 2 intact
and reuses the existing database writer lock. Results include rules version
`pakistan-0-3-v3.4`; changing rules requires bumping that version. Content or lifecycle
changes invalidate saved results, which appear as UNVERIFIED until `process`
runs again. Metadata-only refreshes do not force reprocessing. Source variants
retain separate evidence; differing eligibility outcomes become UNVERIFIED.
When sources disagree, a definite exclusion outranks another source's missing
evidence for that same dimension. Opposing positive and negative claims remain
UNVERIFIED. Each source's quote and identity remain in `variants`, and the
combined decision appears in `merged_decisions`.
The additive `job_classification_history` migration backs up the database and
copies every existing classification unchanged before new rules run. SQLite
triggers append each new/revised result; unchanged runs add nothing. Historical
snapshots survive job deduplication and retain source identities in their JSON.
Current results still cascade safely during merges; surviving changed jobs are
reprocessed on the next run. History can be inspected with SQL:

```sql
SELECT job_id, rules_version, processed_at, result
FROM job_classification_history WHERE job_id = 1 ORDER BY id;
```

Normal results use the existing active/listed/unexpired discovery policy.
`--include-history` exposes closed, expired, unknown-lifecycle, and unlisted records
with their lifecycle state; eligibility never reactivates them. In particular,
legacy jobs migrated with unknown lifecycle stay hidden until collection confirms
them. Opening storage continues its existing expiry evaluation; processing does
not rewrite collected job content or source metadata.
