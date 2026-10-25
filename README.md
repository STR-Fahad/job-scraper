# Personal Job Collection Module

Python 3.13, HTTPX, BeautifulSoup, Pydantic and SQLite. Six public-API collectors
share an interface, validated records, host pacing, transactional persistence and
lifecycle tracking. No LLM analysis, CV matching, dashboard or application submission.

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
