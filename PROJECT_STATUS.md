# Job Scraper — Current Status Snapshot

**Observed 2026-11-01 (Asia/Karachi); scoring calibration added 2026-11-02.** Recheck before acting; this is a snapshot, not a permanent count. See `PROJECT_HANDBOOK.md` for the architecture and rules.

## 2026-11-04 — Module 2/3 parsing and restriction validation (committed with `91ecfe3`, tag `module-2-3-parsing-restriction-validation`)

| Item | Result |
|---|---|
| Module 2 | `pakistan-0-3-v3.6`: number-word and bracketed experience minimums (`five years`, `[3+] years`) with contract, probation, recency, degree-alternative and approximate-wording guards; hyphenated `-based` compounds and `based on/upon` no longer create geography exceptions, while `UK-based`, `US-based`, `Berlin-based`, `non-US-based` and explicit country exclusions still do. Database reclassified offline (2,985 jobs; ELIGIBLE 0 / INELIGIBLE 806 / UNVERIFIED 2,179; matching population 781) from verified backups; history preserved |
| Module 3 | `requirements-v3`: compensation-only geography, equal-opportunity text and bare labels may be `nonreq`; explicit hiring, residency, work-authorization and `only available/limited to` wording cannot be discarded, even mixed with salary text; claim quotes cut at word boundaries. Prompt `match-v9`, schema `analysis-v7`, `score-v6`, `evidence-v9` unchanged; rule hash `0dee3c1c25182af7` |
| Live results | One request for #2932 failed validation (R034, then R051 `nonreq`; nothing saved). One request for #2919 succeeded: assessment #4, score 0/100, low confidence, coverage 15% (profile lists Git/CI/AI tools without narrative evidence; see `work/MODULE_3_EVIDENCE_QUALITY_AUDIT.md`) |
| Tests | Full suite **658 passed**; Ruff, `pip check`, `git diff --check` clean; SQLite integrity ok, foreign keys 0 |
| Known limits | Detector is phrase based; model may label location-policy text `nonreq` (GitLab R051); Module 3B experience parser does not read written number words; Pakistan eligibility remains UNVERIFIED for all jobs; approved profile unchanged at 15 months |

## 2026-11-02 — Experience final compatibility review (uncommitted)

| Item | Observed value |
|---|---|
| Hashing | `profile_hash` is representation independent: 1.25 years = 15 months, 1.5 = 18, 2.0 = 24 hash identically; all 961 month counts 0-960 hash differently; the real v1 and v2 stored hashes recompute exactly. A changed approved amount makes assessments stale (tested at 16, 18 and 24 months) |
| Versions | Experience resolution changed materially against evidence-v8, so **`EVIDENCE_VERSION` is now `evidence-v9` and `SCORING_VERSION` `score-v6`**. Assessments #1 (score-v4) and #2 (score-v5) are stale and preserved |
| Fixes | Month-based minimums were being dropped from the score (a bare quote lost its context); preferred, maximum and "or equivalent" experience lines were scored as mandatory minimums (also under evidence-v8); both fixed |
| Real database | Backup `data/backups/jobs-pre-experience-rescore-20261101T215642Z.sqlite3` (verified identical, integrity ok). New offline row **#3** for job 22 (`offline_rescore`, score-v6/evidence-v9, score 2, token usage `{}`, source #2); rows #1 and #2 byte-identical; every other table unchanged; integrity ok, 0 FK violations |
| Job 22 | approved 15 months, required 24, gap 9 months, experience UNMATCHED, Module 2 UNVERIFIED, only credit still the Python project PARTIAL |
| Tests | Full suite **548 passed**; Ruff, `pip check`, `git diff --check` clean |
| Live calls | None |

Report: ignored `work/EXPERIENCE_FINAL_COMPATIBILITY_REVIEW.md`. The "no version bump" decision recorded in the section below was reversed by this review.

## 2026-11-02 — Experience in years and months (uncommitted)

| Item | Observed value |
|---|---|
| Behavior | Candidate experience is held and compared as whole months and shown as years and months. Approved profile v2 (`experience_years: 1.25`, unchanged in the database) reads as **15 months = 1 year, 3 months**. Job 22's 2+ years is **24 months**; gap **9 months**; still UNMATCHED |
| Compatibility | No migration. Legacy decimal years convert exactly (inexact values round down and are reported). `profile_hash` is representation-independent: v2's stored hash recomputes exactly, and re-approving in months keeps it. Score-v5 / evidence-v8 / rule hashes unchanged; replay of job 22's saved classifications is identical to assessment #2 |
| Code | New `job_scraper/experience.py`; `candidate.py`, `evidence.py`, `requirements.py`, `matching.py`, `ranking.py` updated; Module 2 untouched |
| Tests | New `tests/test_experience.py` (25 test functions, 71 cases); full suite **520 passed**; Ruff check/format and `pip check` clean |
| Real database | Read-only plus scratch copies. All table hashes identical to the earlier baseline; integrity ok; no review table; assessments #1 (score-v4) and #2 (score-v5) unchanged |

Report: ignored `work/EXPERIENCE_YEARS_MONTHS_REPORT.md`.

## 2026-11-02 — Final 3B.2 eligibility-review safety refinements (uncommitted)

| Item | Observed value |
|---|---|
| Behavior | A current evidence-backed `confirmed_restricted` review moves a job to a separate non-recommended section (no rank, not queued for assessment; scores/assessments/reviews kept). Reviews carry the job fingerprint; a changed job makes them `needs_reverification` (derived at read time, rows never rewritten); restored when content returns or a new review is added. Evidence is the user's claim: never fetched or verified |
| Code | `reviews.py` (`fingerprints`, `annotate`, `label`, derived `currency`), `ranking.py` (restricted section, queue exclusion, flags, shared renderers), `cli.py` (`--only-restricted`, `review list --outdated`), `processing.snapshots(job_id)` optional filter |
| Tests | `tests/test_ranking.py` now 40 tests (15 new safety tests); full suite **449 passed**; Ruff check/format (50 files), `pip check`, `git diff --check` clean |
| Real database | Read-only commands only. 350 jobs, population 2, 1 current assessment (job 22, #2, score-v5, score 2, coverage 29%), job 22 Module 2 **UNVERIFIED**, score-v4 row #1 retained and stale. All tables identical before and after; integrity ok, 0 FK violations. No review table yet (created by the first `review add` after a backup) |
| Live calls | None |

Report: ignored `work/MODULE_3B2_FINAL_SAFETY_REVIEW.md`.

## 2026-11-02 — Module 3B.2 local rankings and manual review (uncommitted)

| Item | Observed value |
|---|---|
| Code | New `job_scraper/ranking.py` and `job_scraper/reviews.py`; CLI `rank list/explain/queue` and `review list/show/add` in `cli.py` |
| Schema | New append-only `eligibility_reviews` table, created lazily by the first `review add` after a verified backup. **Not yet created in `data/jobs.sqlite3`** (no review was entered); verified on a scratch copy |
| Tests | New `tests/test_ranking.py` (25 tests); full suite **434 passed**; Ruff check/format (50 files) clean |
| Real database (read-only commands) | 350 jobs; ranking population **2** (jobs 21, 22); **1** current assessment (job 22: assessment #2, score 2, coverage 29%); **1** awaiting assessment (job 21, missing); 0 stale. All tables, hashes and the backup folder unchanged after running every `rank`/`review` command; integrity ok, 0 FK violations |
| Live calls | None; no collection |

Report: ignored `work/MODULE_3B2_RANKING_REVIEW.md`. Limits: one real scored job; rankings are structurally verified with synthetic fixtures only; duty credit is still conservative.

## 2026-11-02 — Final 3B.1 audit and offline rescore (committed with `58c7524`)

| Item | Observed value |
|---|---|
| Code | `matching.rescore` plus shared `_assessment`/`_save`/`_approved_profile` helpers; CLI `match rescore`; `results` shows `assessment_mode`/`provenance` |
| Tests | New `tests/test_offline_rescore.py` (9 tests); full suite **409 passed**; Ruff check/format (47 files), `pip check`, `git diff --check` clean |
| Database write | Backup `data/backups/jobs-pre-score-v5-rescore-20261101T205759Z.sqlite3` (integrity ok, identical rows) taken first. Added `ai_assessments` row **2** for job 22: offline_rescore, score-v5, score **2**, coverage 29%, ceiling 73, `low_score_unresolved_evidence`, source assessment 1, token usage `{}` |
| Preservation | Row 1 (live, score-v4, score 0) byte-identical and reported stale; jobs, sources, classifications, history, profiles and lifecycle events unchanged; integrity ok, 0 FK violations |
| Live calls | None |

Report: ignored `work/MODULE_3B1_FINAL_SCORING_AUDIT.md`. Module 3B.1 is complete for manual-review use; next is Module 3B.2.

## 2026-11-02 — Module 3B.1 scoring calibration (uncommitted)

| Item | Observed value |
|---|---|
| HEAD | still `ae97cf7`; changes are uncommitted and unpushed |
| Code changed | `job_scraper/evidence.py` (evidence-v8), `job_scraper/matching.py` (score-v5), `job_scraper/requirements.py` (shared technology list only; requirements-v2 unchanged) |
| Tests | New `tests/test_scoring_calibration.py` (34 tests) and `tests/fixtures/scoring_calibration.json` (17 synthetic cases); 4 existing tests updated for the new semantics. Full suite **400 passed** |
| Checks | Ruff check passed; Ruff format: 46 files formatted; `pip check`: no broken requirements; `git diff --check`: clean |
| Database | Read-only: `integrity_check=ok`, 0 foreign-key violations; jobs, sources, classifications, history, profiles and lifecycle events identical to the pre-job-22 backup; the score-v4 job-22 assessment (score 0) is retained and reported **stale** |
| Live calls | None |

Behavior: headline score uses applicable category weight; separate `confidence` (evidence coverage, score ceiling, interpretation); explicit OR groups scored once; project evidence matches unqualified skill mentions but is PARTIAL against professional/proficiency wording; technology-specific years are not inferred from total years. Offline replay of job 22's saved classifications under the new rules: **score 2/100**, evidence coverage 29%, ceiling 73, `low_score_unresolved_evidence`; the 2+ years requirement stays UNMATCHED and Module 2 eligibility stays UNVERIFIED. Details: ignored `work/MODULE_3B1_SCORING_CALIBRATION.md`.

## Repository and verification

| Item | Observed value |
|---|---|
| Local project | `C:\job scraper` |
| GitHub origin | `https://github.com/STR-Fahad/job-scraper.git` |
| Branch / HEAD before documentation | `main` / `ae97cf7 MODULE_3B1_SCORING` |
| Recent commits | `ae97cf7`, `5dcb7ea`, `be9846e`, `2eef508`, `28b54d2` |
| Working tree before documentation | Clean (`git status --short` empty) |
| Documentation changes this task | New `PROJECT_HANDBOOK.md`, `AGENTS.md`, `PROJECT_STATUS.md`; uncommitted, unpushed |
| Automated tests | `366 passed` via `.\.venv\Scripts\python.exe -m pytest -q` |
| Lint / format / dependencies | Ruff check passed; Ruff format check: 45 files formatted; `pip check`: no broken requirements |
| Local database | `data/jobs.sqlite3`, schema `user_version=2`, `PRAGMA integrity_check=ok` |

The documentation task made **no application code or configuration changes**. Tests are mocked for ordinary Gemini assessment paths; they do not imply live ranking accuracy.

## Implemented versus planned

| Component | Status and execution evidence |
|---|---|
| Module 1 collection | Six collectors implemented. Earlier live feed checks are documented in `HARDENING_REPORT.md`; current live freshness was not reverified during this documentation task. Lifecycle, source registry/discovery and safe migrations exist. |
| Module 2 screening | Implemented in `screening.py`/`processing.py`, with extensive regressions. Current rule version `pakistan-0-3-v3.6`. |
| Module 3A Gemini | Official `google-genai` integration and synthetic connectivity probe implemented. No connectivity probe run in this documentation task. |
| Module 3B.1 | CV review/profile versioning, numbered requirements, role-only Gemini classification, Python evidence and deterministic score, SQLite cache implemented. One real job-22 assessment succeeded on 2026-10-29 (report in ignored `work/MODULE_3B1_FINAL_LIVE_TEST.md`). |
| Module 3B.2 | Implemented offline and uncommitted (see the 2026-11-02 sections): local ranking, assessment queue, explanations, append-only manual eligibility review, restricted-job separation and outdated-review detection. |
| Module 4 / Module 5 | Dashboard/tracking and scheduling/notifications not implemented. |

## Local SQLite snapshot

Read-only query on 2026-11-01: **350** logical jobs; **350** source observations; **30 active**, **320 unknown** source lifecycles; **2** scans; **30** lifecycle events; **350** current classifications (**118 INELIGIBLE**, **232 UNVERIFIED**, **0 ELIGIBLE**); **2,010** classification history rows; **2** candidate profile versions; **1** AI assessment. `PRAGMA integrity_check=ok`. These figures reflect this laptop's current database, not a repository fixture or live market total. The single real assessment is for job 22; its Module 2 Pakistan eligibility remained UNVERIFIED.

## Confirmed limitations and questions

- **Confirmed:** the real job-22 assessment validated all 30 numbered items and saved score **0/100** (all four components zero), with 1 UNMATCHED 2+ year minimum versus approved 1.25 years (now shown as 15 months, 1 year 3 months) and 33 UNVERIFIED findings. Cache lookup used zero further SDK calls. This confirms the pipeline, not calibration.
- **Confirmed:** alternative language clauses remain unverified; listed-only Docker/Linux/Git do not prove proficiency. Current duty alignment found no supported credit in that one job. Scores are not hiring probabilities or eligibility decisions.
- **Confirmed design bounds:** English rule parsing and finite technology aliases can miss unusual or multilingual terms; CV redaction is heuristic; descriptions over 12,000 characters fail before matching. Source-observation active status is not guaranteed employer availability.
- **Unverified here:** current live state of all six external APIs, Gemini billing/model access today, ranking accuracy across a representative set, and whether other agents changed this tree after the snapshot.
- **Potential improvement, not a confirmed defect:** calibrate transferable-duty scoring and alternative qualifications using reviewed examples before ranking. Do not change rules solely to raise job 22's score.

## Next bounded tasks

1. Scoring calibration and offline rescore are done (see the 2026-11-02 sections). Job 22 needs no new live call for the rule change. Next: Module 3B.2 ranking and manual eligibility review (scope in the handbook roadmap).
2. Assess more relevant jobs so rankings become informative: `rank queue` lists them; `match analyze --live` is billable and needs your explicit authorization per batch. Then review real rankings by hand before Module 4.
3. Later, consider a local dashboard/application tracker; then opt-in scheduling/notifications. Neither is currently implemented or authorized by this document.

Historical notes under ignored `work/` and `HARDENING_REPORT.md` provide evidence for earlier iterations. Current source and tests are authoritative when they differ. Before new work, run `git status --short`, inspect recent commits and the relevant code, and update this dated snapshot only with executed evidence.

**Post-commit upkeep:** After every project commit, the next AI agent must review that commit and update this snapshot and `PROJECT_HANDBOOK.md` with its hash, date, implementation impact, test evidence, and remaining limitations. If a documentation commit is needed, obtain the user's authorization before committing or pushing it.
