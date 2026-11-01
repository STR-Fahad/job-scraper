# Job Scraper — Current Status Snapshot

**Observed 2026-11-01 (Asia/Karachi); scoring calibration added 2026-11-02.** Recheck before acting; this is a snapshot, not a permanent count. See `PROJECT_HANDBOOK.md` for the architecture and rules.

## 2026-11-02 — Final 3B.1 audit and offline rescore (uncommitted)

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
| Module 2 screening | Implemented in `screening.py`/`processing.py`, with extensive regressions. Current rule version `pakistan-0-3-v3.4`. |
| Module 3A Gemini | Official `google-genai` integration and synthetic connectivity probe implemented. No connectivity probe run in this documentation task. |
| Module 3B.1 | CV review/profile versioning, numbered requirements, role-only Gemini classification, Python evidence and deterministic score, SQLite cache implemented. One real job-22 assessment succeeded on 2026-10-29 (report in ignored `work/MODULE_3B1_FINAL_LIVE_TEST.md`). |
| Module 3B.2 | Planned; ranking and eligibility review not implemented. |
| Module 4 / Module 5 | Dashboard/tracking and scheduling/notifications not implemented. |

## Local SQLite snapshot

Read-only query on 2026-11-01: **350** logical jobs; **350** source observations; **30 active**, **320 unknown** source lifecycles; **2** scans; **30** lifecycle events; **350** current classifications (**118 INELIGIBLE**, **232 UNVERIFIED**, **0 ELIGIBLE**); **2,010** classification history rows; **2** candidate profile versions; **1** AI assessment. `PRAGMA integrity_check=ok`. These figures reflect this laptop's current database, not a repository fixture or live market total. The single real assessment is for job 22; its Module 2 Pakistan eligibility remained UNVERIFIED.

## Confirmed limitations and questions

- **Confirmed:** the real job-22 assessment validated all 30 numbered items and saved score **0/100** (all four components zero), with 1 UNMATCHED 2+ year minimum versus approved 1.25 years and 33 UNVERIFIED findings. Cache lookup used zero further SDK calls. This confirms the pipeline, not calibration.
- **Confirmed:** alternative language clauses remain unverified; listed-only Docker/Linux/Git do not prove proficiency. Current duty alignment found no supported credit in that one job. Scores are not hiring probabilities or eligibility decisions.
- **Confirmed design bounds:** English rule parsing and finite technology aliases can miss unusual or multilingual terms; CV redaction is heuristic; descriptions over 12,000 characters fail before matching. Source-observation active status is not guaranteed employer availability.
- **Unverified here:** current live state of all six external APIs, Gemini billing/model access today, ranking accuracy across a representative set, and whether other agents changed this tree after the snapshot.
- **Potential improvement, not a confirmed defect:** calibrate transferable-duty scoring and alternative qualifications using reviewed examples before ranking. Do not change rules solely to raise job 22's score.

## Next bounded tasks

1. Scoring calibration and offline rescore are done (see the 2026-11-02 sections). Job 22 needs no new live call for the rule change. Next: Module 3B.2 ranking and manual eligibility review (scope in the handbook roadmap).
2. After calibration, design Module 3B.2 ranking and manual eligibility review. Acceptance: show score components, unresolved conditions, lifecycle/staleness, no automatic ELIGIBLE promotion.
3. Later, consider a local dashboard/application tracker; then opt-in scheduling/notifications. Neither is currently implemented or authorized by this document.

Historical notes under ignored `work/` and `HARDENING_REPORT.md` provide evidence for earlier iterations. Current source and tests are authoritative when they differ. Before new work, run `git status --short`, inspect recent commits and the relevant code, and update this dated snapshot only with executed evidence.

**Post-commit upkeep:** After every project commit, the next AI agent must review that commit and update this snapshot and `PROJECT_HANDBOOK.md` with its hash, date, implementation impact, test evidence, and remaining limitations. If a documentation commit is needed, obtain the user's authorization before committing or pushing it.
