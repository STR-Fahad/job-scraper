# Job Collection Module — Implementation and Validation Report

Validated October 25, 2026 on Windows, Python 3.13.17. No commit or push made.

## Result

Ready for integration with the next module through `Store.list_jobs()`, the `jobs`
CLI, or the `discovery_jobs` view. Use the canonical `workplace_type`; do not build
normal discovery against raw historical tables. This is a source-observation
archive: closure evidence does not independently prove employer availability.

## Defects fixed

- Canonical remote/hybrid/onsite/unknown classification; unspecified never becomes
  onsite. Source workplace values and separate eligibility restrictions retained.
- Greenhouse detail `first_published` now maps to UTC `posted_at`, independently of
  `updated_at`. All four live example postings now have correct publication dates.
- Ashby unlisted postings are retained for history/direct access and hidden from
  ordinary discovery. Visible duplicates are not hidden by an unlisted copy.
- Host-wide Retry-After deadlines handle seconds and HTTP dates, persist across
  runs, and apply across employer boards even after retries are exhausted. Long
  waits defer the host rather than sleeping for minutes.
- Ashby base salaries and Greenhouse cents-based compensation are normalized.
  Bonuses/equity remain separate; ambiguous amounts/periods stay null.

## Features added

- Per-source active/closed/expired/unknown lifecycle, check timestamps, scan records
  and append-only transition evidence. Reappearance reactivates a posting.
- Disappearance closure only after successful exhaustive scans. Partial, invalid,
  failed, skipped and interrupted scans cannot close missing jobs. Empty feeds
  require consecutive successful confirmations separated by a configurable delay.
- Explicit expiry handling, without treating application deadlines as closure.
- Seeded company-board discovery from public ATS links or explicitly permitted
  careers-page HTML links/iframes. Endpoint verification, provenance, deduplication,
  pending approval and enable/disable controls. No global company-search endpoint.
- Verified disabled example boards: Enveritas, Ashby, Spreetail. All three were
  independently verified through the new discovery CLI and saved with timestamps.
- An OS-held single-writer lock per database, automatically released on process
  exit. Concurrent writer behavior verified using a separate Python process.
- Transactional schema migration with SQLite backups and a normal discovery view.

## Automated verification

- **119 tests passed** (the original 64 plus 55 additional cases).
- Ruff lint passed; formatting check passed; dependency consistency passed.
- Tests cover normalization, salary component separation, unlisted filtering,
  shared/persisted host cooldowns, lifecycle closure/reopening/expiry, empty feeds,
  partial/interrupted scans, legacy migration, migration rollback, source identity
  and URL deduplication, discovery approval/robots/private-host controls, and
  concurrent-process exclusion.
- No tests deliberately generate real server throttling or bypass access controls.

## Live collection

First CLI collection exited 0. The subsequent CLI collection also exited 0.

| Source | Pages, first run | Observations | Unique jobs inserted | Exhaustive |
| --- | ---: | ---: | ---: | --- |
| Himalayas | 5 | 100 | 100 | No; intentional cap |
| Greenhouse / Enveritas | 1 plus details | 4 | 4 | Yes |
| Ashby / Ashby | 1 | 67 | 67 | Yes |
| Lever / Spreetail | 1 | 38 | 38 | Yes |
| Remotive | 1 | 10 | 10 | Yes, available public response |
| Arbeitnow | 25 | 5,098 | 5,079 | Yes |

First-run totals: **5,298 inserted, 9 updated, 10 unchanged, 0 rejected,
0 closed, 0 expired, 0 errors**. Arbeitnow repeated 19 source IDs during traversal;
upserts correctly avoided extra job rows.

Second-run totals: **0 inserted, 0 updated, 709 unchanged, 0 rejected,
0 closed, 0 expired, 0 errors**. It repeated the three employer boards and two
Arbeitnow pages. Himalayas and Remotive correctly honored existing cooldowns and
were skipped. Their immediate second live fetch was not forced.

A separate offline replay of the complete normalized live dataset produced
**5,298 inserts on pass one, then 5,298 unchanged on pass two**, covering all six
sources without additional provider requests. Integrity: `ok`; foreign-key errors: 0.

Samples from every source were inspected. All 67 Ashby salaries populated numeric
base-salary fields. Example: EUR 150,000–250,000 annually, with bonus/equity separate.
Example Greenhouse date: `2024-03-15T13:05:35-04:00` became
`2024-03-15T17:05:35+00:00`, with the original retained.

Live workplace counts: Himalayas 100 remote; Remotive 10 remote; Ashby 67 remote;
Lever 19 remote and 19 onsite; Arbeitnow 365 remote and 4,714 unknown;
Greenhouse 4 unknown. Hybrid/unspecified combinations were verified with controlled
fixtures because the chosen live boards did not supply every combination.

## Original database migration

Original backup:
`C:\job scraper\data\backups\jobs-pre-hardening-20261025T183255Z.sqlite3`.
The migration also created its own automatic v1 backup in the same directory.

- Schema v1 → v2 completed transactionally.
- **330 original jobs and 330 source records preserved.**
- Compared all raw payloads, response metadata and first/last-seen timestamps
  against the pre-hardening backup: exact preservation confirmed.
- SQLite integrity `ok`; no foreign-key violations.
- Legacy lifecycle starts **unknown**, rather than guessing current activity.
  The next successful collection establishes current observations. Historical
  records remain accessible through `--include-history`.
- Live validation data is separate from the original database.

## Changed files

Updated modules under `C:\job scraper\job_scraper`:
`models.py`, `http.py`, `storage.py`, `runner.py`, `config.py`, `cli.py`, and
`collectors/base.py`, `collectors/greenhouse.py`, `collectors/ashby.py`,
`collectors/lever.py`, `collectors/himalayas.py`, `collectors/remotive.py`,
`collectors/arbeitnow.py`.

New modules: `normalization.py`, `migrations.py`, `locking.py`, `discovery.py`,
`registry.py`. New regression files: `tests/test_hardening.py`,
`tests/test_discovery.py`. Updated `config.toml` and `README.md`; added this report.
Existing collector structure and the original tests were retained.

## Evidence locations

All under `C:\job scraper\work\hardening`:

- `first.json`, `second.json`: actual CLI statistics.
- `first.log`, `second.log`: actual collection logs.
- `first-execution.json`, `second-execution.json`: commands, timestamps, exit codes.
- `migration-result.json`: comparison against the original backup.
- `inspection.json`: normalized samples, workplace counts and replay statistics.
- `live.sqlite3`, `replay.sqlite3`: isolated validation databases.

## Remaining limits and unverified behavior

- Himalayas traversal was capped at five pages, not its entire feed.
- EU Lever routing is fixture-tested, not live-tested in this run.
- Greenhouse's chosen live board had no nonempty pay ranges; its salary mapping
  is verified with controlled fixtures rather than a live nonempty range.
- Unlisted Ashby and hybrid fixtures passed; chosen live examples contained no
  unlisted/hybrid cases. Real 429 responses were not intentionally provoked.
- Permitted custom careers-page extraction is fixture-tested; live discovery
  validation used direct public ATS links. JavaScript-only links need a direct URL.
- Discovery starts from supplied URLs; it does not find every company on the web.
- Feed paging is not an atomic provider snapshot. Jobs can change during a scan.
- Single-writer coordination and persistent host cooldowns apply per database.
  Separate installations/databases must coordinate their own provider usage.
- No LLM analysis, CV matching or dashboard was added.

## Git status

The repository already had uncommitted/untracked application files when this work
began. `README.md` is modified; configuration, Python package, tests, packaging
files and this report are untracked relative to the original README-only commit.
Runtime databases, backups and scratch evidence are ignored. Nothing was staged,
committed or pushed. Review this report and the local changes before deciding
whether to commit.
