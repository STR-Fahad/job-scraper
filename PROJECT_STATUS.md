# Job Scraper — Current Status Snapshot

**Observed 2026-11-01 (Asia/Karachi); updated through Module 4 on 2026-11-04.** Recheck before acting; this is a snapshot, not a permanent count. See `PROJECT_HANDBOOK.md` for the architecture and rules.

## 2026-11-24 — Link check for removed postings and 15 company boards (commit pending hash below)

| Item | Result |
|---|---|
| Added | `job_scraper/linkcheck.py`, `HttpClient.probe` (returns a page response without raising for 404/redirects/5xx), the `check-links` command (dry run by default; `--apply`, `--limit`, `--source`, `--interval`, `--json`) and `tests/test_linkcheck.py` (47 tests). Rules are in the handbook |
| Behavior | Checks each open source's `source_url`; REMOVED only on 404/410, a redirect to a generic board page or a closed marker; closes a source only after two REMOVED checks on different days (12 hours apart minimum); every result is a `lifecycle_events` row with the evidence; nothing is deleted; Module 2/3 rules and the judge are untouched; `apply` takes a verified backup first |
| Real-data tests | Dry run on 150 real pages (nothing written): 114 live, 34 removed on the first check (30 Arbeitnow HTTP 410, 2 Remotive 404, 1 Ashby 404, 1 Greenhouse redirect to `?error=true`), 2 unknown (HTTP 403, Himalayas and Jobicy); four removed pages re-fetched by hand matched, and both postings the user had reported removed (ecosio, ElevenLabs) were found. Apply test on a COPY of the database with the clock moved 25 hours: day 1 recorded 11 removed first checks, day 2 closed those 11; integrity ok, 6,897 jobs kept. Each `--apply` run saves a full backup (about 500 MB), so the backup policy is still open |
| Earlier dry run | `check-links --limit 12` (read-only, nothing written, 12 pages fetched): 12 checked, 6 live, 6 removed on the first check (HTTP 404 on Remotive, 410 on Arbeitnow, including job 552 Lemon.io). Himalayas answered 403 to all 15 checked in an earlier run, so those are UNKNOWN (the project never bypasses a block) |
| Verification | Full suite **1016 passed** (also with `-W error::ResourceWarning`); Ruff check and format, `pip check` and `git diff --check` clean |
| Boards added | Greenhouse: Mozilla, Grafana Labs, GitLab, Fivetran, Coinbase, Customer.io, Bitwarden, PlanetScale, Wikimedia; Ashby: Temporal, 1Password, Render, Railway, PostHog, ClickHouse. Collected with 0 errors (about 1,512 new jobs; database 6,897 jobs; verified backup `data/backups/jobs-pre-boards-20261124T125705Z.sqlite3`). Module 2 and judging have NOT run on them yet (the user wants to change rules first) |
| Not verified | No `--apply` run on the real database yet (a collection run was holding the writer lock); the closed-marker phrases are not checked against live closed pages; Himalayas cannot be checked (403); no scheduled run |

## 2026-11-24 — Remote OK and We Work Remotely removed; answers copy; override approved (commit `ffa2d1d`; tag `disable-remoteok-weworkremotely`)

| Item | Result |
|---|---|
| Sources | `remoteok` and `weworkremotely` set `enabled = false` in `config.toml`; they are no longer collected. The user's reason: the user works remotely and wanted those two aggregators gone |
| Deletion | 271 jobs deleted (Remote OK 97, We Work Remotely 174; none also from another source, none tracked as applied) with 271 sources/URLs/classifications/lifecycle events, 89 saved verdicts and 813 classification-history rows. Verified backup first: `data/backups/jobs-pre-delete-sites-20261124T124325653220Z.sqlite3` (git-ignored). After: integrity ok, 0 foreign-key problems, 0 orphan assessments, 5,385 jobs; pool 1,122; at that time Eligible 9 / Ineligible 555 / Unverified 139. Saved verdict files under `work/judgments` stay on disk. `fit_feedback` is append-only (trigger), so 5 answers now refer to deleted jobs; reports still work |
| Answers copy | `work/save_answers.py` (git-ignored) writes `data/answers/answers.sqlite3` (+ CSVs): every Good fit / Not for me answer with reasons, note, the tool's tag at the time and the job's title, company, source and URLs; also applications and their history; indexed by job, verdict, company, source and URL. 16 answer rows, 4 applications at the time. Re-run before any bulk deletion. `work/delete_sites.py` (git-ignored) was the one-off deletion script |
| User activity | The user applied to Supabase, GitLab and Wise Pelican jobs on 2026-11-24 and changed the Canonical frontend job from Applied to Saved; Not for me notes covered removed postings, a paid site, mobile/SQL/infra roles |
| Override | Judge proposal #1 for job 552 (company age read as a 10-year minimum) approved by the user as override #2; the job is now UNVERIFIED (leadership requirement not evidenced), not Eligible |
| Verification | 969 tests passed with this config change; main database integrity ok. A judge session (`judge-continuous-3`) was writing at the same time |
| Known limits | Deleted data is only recoverable from the backup; the answers copy is not synced automatically |

## 2026-11-20 — Wellness check and backup-connection fix (commit `2c19365`; tag `close-backup-connections`)

| Item | Result |
|---|---|
| Check | Full suite, Ruff check and format, `pip check`, `git diff --check`, main database integrity (ok, 0 foreign-key problems, no orphan assessments), offline CLI commands and every dashboard page (200, no server errors, POST without token refused 403) |
| Bug fixed | `with sqlite3.connect(backup) as target:` only commits; it never closes the connection. The eight backup helpers (`feedback`, `judge`, `matching`, `migrations`, `overrides`, `processing`, `reviews`, `tracking`) left each backup file open until garbage collection. They now use `contextlib.closing`. Eight test files had the same pattern; writers there use `closing(...) as db, db:` to keep the commit |
| Verification | **969 passed**, also with `-W error::ResourceWarning` (was about 178 unclosed-database warnings) |
| Docs | The earlier notes saying `fit_feedback` and `blocker_overrides` were not created are corrected: both exist (13 feedback log rows; 1 judge override proposal for job 552, not decided) |
| Not tested | Live collection and Gemini calls (network / billable) |

## 2026-11-20 — `good_enough_fit` tightened (commit `bf09f03`)

| Item | Result |
|---|---|
| Commit | `bf09f03` MODULE_3_GOOD_ENOUGH_FIT_TIGHTENED on `main` (`decision.py`, `tests/test_decision.py`). Tag `good-enough-fit-tightened` |
| Behavior | `good_enough_fit` is withheld from senior/management titles and profile-gap titles (SRE, DevOps, infrastructure, Linux, Kubernetes, containers, mobile, embedded, sustaining, data centre, DBA, SQL developer); those jobs stay UNVERIFIED, never Ineligible. Derived at read time: no database change, no saved verdict changes |
| Effect on the user's 11 answers | 4 of 5 jobs still tagged Eligible were confirmed Good fit; the 5 rejected infra/mobile/SQL jobs moved to Unverified. Remaining miss: Nexoya Node.js backend. Tags at the last re-rank: 727 judged, Eligible 11 / Ineligible 560 / Unverified 156, 461 awaiting |
| Verification | Full suite **969 passed**; Ruff check and format, `pip check` and `git diff --check` clean |
| Known limits | Title-based keywords can miss or over-block; restart the dashboard server to see the rule; judging is stopped |

## 2026-11-20 — Module 2 reads bare region locations, verdict cache decoupled (commit `718fc32`)

| Item | Result |
|---|---|
| Commit | `718fc32` MODULE_2_BARE_LOCATION_REGIONS_AND_VERDICT_CACHE on `main` (`screening.py`, `processing.py`, `matching.py` and two test files; documentation follows) |
| Module 2 (`pakistan-0-3-v3.11`) | A location that is only a region or country list, as aggregator feeds write it ("USA Only", "Canada", "LATAM", "Europe", "Switzerland, Australia, Germany"), counts as a region restriction when EVERY label is a known region or country; a list without Pakistan makes the job INELIGIBLE. Cities, unknown text, APAC/Asia and time-zone text ("CET (+/- 3 hours)") stay UNVERIFIED. A posting that itself says the role can be done from anywhere ("work remotely from anywhere in the world", "hiring worldwide", "open to candidates in any country") overrides a bare country (`OPEN_HIRING`), while "anywhere in Europe" is a limit |
| Found by a test | A separate test database (`data/loc-test/`, git-ignored) built from about 1,800 real jobs showed the first version wrongly excluded 16 jobs whose posting opens the role to anywhere; fixed before the final apply |
| Module 3 cache | The verdict cache key now uses a fixed screening basis (`matching.SCREENING_VERSION` = `pakistan-0-3-v3.9`) instead of `processing.VERSION`; a job's own Module 2 status is already in its fingerprint, so a Module 2 rules change no longer stales every saved verdict |
| Effect on the real data | Backups taken before each run (`data/backups/jobs-pre-module2-v3.10b-*`, `jobs-pre-module2-v3.11-*`, git-ignored). 191 jobs moved from Unverified to Ineligible at Module 2 (about half Arbeitnow bare countries, the rest Jobicy, We Work Remotely and others); 54 of the first-version 207 had already been judged and leave the pool; all saved verdicts stayed current (stale 0); pool 1,188 jobs, 556 awaiting a verdict at that time; tags at that time Eligible 18 / Ineligible 477 / Unverified 137 (632 current). Judging continued afterwards: the plan counter showed 780 judged jobs and 430 plan jobs left |
| Verification | Full suite **952 passed** (25 new tests: bare-location cases, the anywhere-posting override, the fixed cache key); Ruff, `pip check` and `git diff --check` clean; main database integrity ok, 0 foreign-key violations |
| Known limits / next | Time-zone locations and APAC/Asia stay Unverified; "work from anywhere with the setup that suits you" style benefit text counts as open (cautious: the job stays Unverified); the judge still cannot quote the location field itself (the remaining part of the planned location-field change); `good_enough_fit` still lets weak core-stack matches through (7 of 11 answered tags were rejected) |

## 2026-11-20 — Reasons and a note on Good fit / Not for me (commit `aa26edc`)

| Item | Result |
|---|---|
| Commit | `aa26edc` DASHBOARD_FIT_REASONS_AND_NOTE on `main` (`feedback.py`, the dashboard `app.py`, `focus.py`, `render.py`, `tests/test_feedback.py`; documentation follows) |
| Behavior | An opened job card has a "Your answer (optional reason)" form: tick one or more reasons (too senior, location or timezone, language, not my stack, not a software role, on-site or hybrid, company or pay, other), add an optional note (300 characters) and press Good fit or Not for me; the quick buttons still answer without a reason. The card shows the reasons and note and keeps the boxes ticked. The Calibration page and `feedback report` add a "Why you said Not for me" table split by the tool's tag at the time |
| Storage | `fit_feedback` has a `reasons` column (comma-separated keys in a fixed order, validated); an earlier table without it is migrated by `ALTER TABLE` after a verified backup. The table now exists in the real database (created on the first click; 13 log rows at 2026-11-20). A pick never changes a tag |
| Verification | Full suite **927 passed** (3 new); Ruff check and format, `pip check` and `git diff --check` clean; checked in the running dashboard (To review tab, opened card form) |
| Known limits | No answers exist yet, so the report is empty; an error rate needs about 50 answers. The dashboard must be restarted after code changes (a stale process kept old code once) |

## 2026-11-20 — Aggregators, blocker overrides and Good fit / Not for me (commit `bc38242`)

| Item | Result |
|---|---|
| Commit | `bc38242` MODULE_1_AGGREGATORS_BLOCKER_OVERRIDES_FIT_FEEDBACK on `main` (code, tests and `config.toml`; documentation follows in the next commit) |
| Module 1 | Four public aggregator collectors: Jobicy (JSON API), Remote OK (JSON; first array entry is a legal notice and is skipped), Working Nomads (JSON) and We Work Remotely (four RSS category feeds; DTD/entity feeds are refused). Each waits 5 s between requests and 6 h between runs; the source name and link are stored on every job (Remote OK and Jobicy ask for credit). 406 jobs were collected into the main database (5,656 jobs in total); Module 2 ran on them (168 Ineligible, 238 Unverified); 110 were software roles in the Module 3 pool |
| Module 3 overrides | `overrides.py` and `override propose / approve / reject / list`: the judge or the user proposes lifting ONE hard blocker (`experience_gap` or `judgment_blocker`) for ONE job with a verbatim quote and a reason; a proposal changes nothing; only the user's approval for the CURRENT assessment lifts that blocker (it goes outdated if the job is judged again) and the tag is derived again from the other rules (never set to Eligible directly). Append-only table `blocker_overrides`, created after a verified backup on the first proposal (now exists in the real database: 1 judge proposal, job 552, awaiting the user's decision at 2026-11-20) |
| Dashboard | New **To review** tab (jobs tagged only `good_enough_fit`), **Good fit** / **Not for me** buttons on every card (Good fit moves a job into Eligible, Not for me into Ineligible, Undo returns it to the tool's tag), an append-only `fit_feedback` record that stores the tag the user saw, a **Calibration** page (More menu) and `feedback report` / `feedback list`. A pick never changes a tag, assessment or tracker status. The table is created after a verified backup on the first click (no answers yet) |
| Judging | In the main database 481 jobs are judged (110 aggregator jobs and 48 more from the plan, plus the earlier 323); final tags at 2026-11-19: Eligible 15 / Ineligible 358 / Unverified 108; 769 pool jobs still await a verdict. Batch 15 (50 jobs) was started in its own judge session on 2026-11-20. The three aggregator Eligible jobs all came from the `good_enough_fit` policy rule, not from the judge's own verdict |
| Aggregator findings | About 49% of the 406 aggregator titles read senior or lead and about 3% junior; 23 of the first 50 verdicts were Unverified with `remote_region_condition_unclear` because the region sits in the feed's location field, which the judge cannot quote |
| Verification | Full suite **924 passed**; Ruff check and format, `pip check` and `git diff --check` clean; main database integrity ok, 0 foreign-key violations; verified backup `data/backups/jobs-pre-aggregators-20261118-112745.sqlite3` (git-ignored) |
| Known limits / next | (1) Planned: let the judge quote a job's location field as an eligibility item (needs care: a Module 2 version bump or changed requirement ids make saved verdicts stale). (2) The next collection should use a different source type (company boards via Greenhouse/Ashby/Lever need no new code). (3) Answer the To review jobs to start collecting calibration evidence; an error rate needs about 50 answers. (4) The Module 2 version label is still `pakistan-0-3-v3.9`; a bump needs a planned re-import of the saved verdict files |

## 2026-11-18 — Calibration fixes `b102413` and more judging (committed 2026-11-18)

| Item | Result |
|---|---|
| Commit | `b102413` MODULE_2_3_CALIBRATION_FIXES on `main` (6 files: `evidence.py`, `experience.py`, `screening.py` and three test files). Tag `module-2-3-calibration-fixes` |
| Fixes | (1) `evidence.py`: a bare "Qualifications" line inside a requirements list is a sub-heading, so postings with "Requirements / Qualifications:" no longer fail `judge-import` with `missing_explicit_requirement_segment`. (2) `screening.py` (Module 2): "Remote in EU, Europe, North America" style lists count as a region restriction when every label is a known region or country; "EU" and "European Union" are known regions. (3) `experience.py`: years tied to a named specialist area ("3+ years in SRE, platform or infrastructure roles") are scoped, not total-experience minimums; generic software wording stays a total minimum |
| Version note | The Module 2 rules version stays `pakistan-0-3-v3.9` on purpose. A first attempt to bump it to v3.10 made all 324 saved verdicts stale (the verdict cache key includes the screening version), so it was reverted and Module 2 re-run; verdicts are current again. Consequence: job 3576 ("Remote in Germany") is now Module 2 INELIGIBLE under the v3.9 label. The clean fix is a version bump plus re-import of the saved verdict files, ideally together with the next Module 2 change |
| Verification | Full suite **885 passed** (8 new tests); Ruff check and format, `pip check`, `git diff --check` clean; main database `integrity_check` ok, 0 foreign-key violations, 5,250 jobs; verified backup `data/backups/jobs-pre-module2-v3.10-20261118-110614.sqlite3` (git-ignored) |
| Judging this session | Judging moved to a rule: it happens in a separate judge session, never in the main chat. In this session 86 more jobs were judged (324 total, 797 plan jobs left, 22 logged skipped). Final tags now Eligible 12 / Ineligible 235 / Unverified 77; none of the new jobs became Eligible |
| Calibration | The user's Google Sheet "Remote Jobs" (jobs tab: green applied, red not hiring; Applied tab: red rejected; Not hiring tab: remote companies with no applicable job at the time, not a blocklist) was compared with the database. Only Canonical, ElevenLabs, Supabase and GitLab overlap. 12 of 27 applied postings were readable and were run through a **separate calibration database** (`data/calibration/calibration.sqlite3`, config `data/calibration/config.toml`, import script `data/calibration/import_postings.py`; all git-ignored under `data/`). Result: the tool tags the user's kind of job (junior full-stack, Python, data) Eligible (5 of 9 judged); weak points were the region text in a header (fix 2), area-scoped years (fix 3) and the heading bug (fix 1) |
| Known limits | 15 of 27 applied postings were unreadable (LinkedIn login, expired links), including 6 of 7 rejections, so tag versus outcome could not be compared; the calibration set is too small for an error rate. Calibration jobs are imported as source `remotive`, board `calibration` only because the tool accepts six sources; the label exists only in the calibration database. Module 2 for those jobs only reads the text given |

## 2026-11-16 — Commits for the work since Module 4 (code and tests committed; documentation follows)

| Item | Result |
|---|---|
| Commits | `b279741` MODULE_2_3_REFRESH_QUALITY_AND_PARSER_FIXES (Module 2 v3.9, requirements-v4, evidence-v12, config boards); `78e1e93` MODULE_3_FINAL_DECISION_AND_SESSION_JUDGMENT (`decision.py` final-v4, `judge.py`, ranking/CLI wiring); `82dd6f8` DASHBOARD_FOCUS_MAIN_PAGE (Focus page, More menu, new colours). All on `main`, not pushed |
| Verification at the committed state | Full suite **877 passed**; Ruff check and format, `pip check` clean; real database `integrity_check` ok, 0 foreign-key violations; 238 judged jobs (Eligible 12 / Ineligible 165 / Unverified 61 under final-v4); profile v3 approved |
| Not committed (intentionally) | `.claude/launch.json` (local preview config), everything under ignored `work/` and `data/` (evidence, judgments, database, backups) |
| Known limits | Judgments of 210 jobs were carried over from profile v2, not re-judged; the policy tags read only what the approved profile shows; Linux/cloud/distributed-systems evidence would resolve more Unverified jobs; location-unclear jobs stay Unverified |

## 2026-11-16 — Fewer Unverified: three approved policy rules, `final-v4` (committed 2026-11-16, see the commit block above)

| Item | Result |
|---|---|
| Why | 132 of 238 judged jobs were Unverified. Analysis: 44 wrong role type, 35 location/office unclear, 7 mostly supported, 10 barely supported, 36 in between; the missing-evidence leaders were Linux (43 jobs), cloud (22), kernel/embedded (20), distributed systems (20), open source (19) |
| Rules (user-approved 2026-11-16, derived at read time in `decision.py`, nothing stored) | **1 out_of_scope**: judgment reason codes name a non-software role type (support, QA, developer relations, research-level, FinOps, hardware enablement) → INELIGIBLE. **3 good_enough_fit**: ≥60% of ≥3 mandatory (`req`) verdicts met/partial, none unmet, no open location/office/language code, score rules not INELIGIBLE → ELIGIBLE. **4 poor_fit**: ≤25% of ≥4 mandatory verdicts supported → INELIGIBLE. Rule 2 (location default) was not adopted: location-unclear jobs stay Unverified |
| Effect on real data | Unverified 132 → 61, Ineligible 101 → 165 (48 out_of_scope, 16 poor_fit), Eligible 5 → 12 (7 good_enough_fit: 2879 Nexoya, 2896 Bromcom, 2918 and 2919 Tether, 5110 and 5124 Canonical, 5182 Supabase). Hard blockers and a confirmed judgment still win; tags name the rule that fired |
| Tests | 4 new decision tests; full suite **877 passed**, Ruff/`pip check`/`git diff --check` clean; dashboard restarted |
| Limits | Policy tags read only what the approved profile shows (missing evidence is not proof of a missing skill); Linux, cloud and similar facts from the owner would resolve more jobs |

## 2026-11-16 — Profile v3 approved by the user and carried-over judgments (committed 2026-11-16, see the commit block above)

| Item | Result |
|---|---|
| Approval | The user approved `work/evidence/PROFILE_V3_FOR_APPROVAL.json`; `profile approve` created **version 3**, hash `10af4f25…`, 15 months unchanged (v2 row untouched). A verified backup `data/backups/jobs-pre-profile-v3-*` was taken first. The profile sanitizer rejected GitHub URLs, so the approved copy cites repos by name; full URLs stay in the evidence report |
| Effect | All 328 earlier assessments became stale (kept). Added `match judge-import --carry-over`: accepts a verdict made under an EARLIER APPROVED profile (never an unknown hash), re-checks every quote against the current profile and labels the row `provenance.carried_over_from_profile_hash`; the Focus card says "judged under an earlier profile; not yet re-judged with v3". 210 of 219 verdict files were carried over (the other 9 are verdicts for jobs logged as skipped because of the parser false positive and never imported). Not a re-judgment |
| State | 210 current judgments; final ELIGIBLE 5 / INELIGIBLE 88 / UNVERIFIED 117 (unchanged); integrity ok, 0 foreign-key violations, 538 assessments; dashboard restarted (Eligible 4 + 1 applied, Unverified 117, Ineligible 88). Suite **873 passed**, Ruff clean |
| Next | Re-judge under v3 the jobs that can gain from the new evidence: the top 15 and P1/P2 jobs in `work/evidence/NARROWING_ANALYSIS.md` (not started; needs the user's go) |

## 2026-11-16 — GitHub evidence analysis and profile v3 DRAFT (read-only; now approved, see above)

| Item | Result |
|---|---|
| Source | GitHub CLI logged in as the owner's personal account (read-only `gh` calls): 23 repos (1 private, solo Rails 8 app), 2 merged PRs (both in the owner's team repo), no org memberships. The Dubizzle work is **not visible** from this account (three public Dubizzle orgs exist; no membership, no SSO header) |
| Output (Git-ignored, under `work/evidence/`) | `PROFILE_V3_DRAFT.json` (schema-valid, every new line tagged `[DRAFT v3 - NOT APPROVED]` with its source URL; professional entries and the 15-month snapshot identical to v2), `PROFILE_V3_EVIDENCE_REPORT.md`, `NARROWING_ANALYSIS.md`, raw data in `github/` |
| Evidence effect | Personal/academic repos are PROJECT evidence only. After the owner's answers (the team repo written by the owner; the private Rails repo is the existing Instagram chat bot project) the deterministic scorer would move CLIP, PEFT, Webhooks to project and add Go, TypeScript, Expo, OpenAPI as project; Docker, PostgreSQL, GitHub Actions, CI/CD, Git/GitHub, testing stay listed-only to it (its project detector is alias-based), though the session judge can cite the narrative. Code check: the chat bot has a pgvector retrieval layer but no tool/function calling, so agents and tool calling stay unproven. `PROFILE_V3_FOR_APPROVAL.json` is an unapproved, tag-free copy; nothing was imported |
| Database | Unchanged: profile v2 still the only approved profile (hash `937592b4ca8e`); no assessment or classification written; no jobs re-judged |
| Open | Facts to confirm before v3 can be approved are listed in section 6 of the report; the Dubizzle work needs the right GitHub account or an export |

## 2026-11-11 — Judging batches 2 and 3, parser fix `evidence-v12` (committed 2026-11-16, see the commit block above)

| Item | Result |
|---|---|
| Batch 2 | 25 jobs sent (Canonical, Supabase non-senior, plus GitLab #2932 and Tether #2919; support, sales and trainer titles dropped); 24 saved, 1 skipped. GitLab #2932 became the 4th ELIGIBLE; everything else UNVERIFIED. The session stopped itself only on a usage check (5-hour limit read at 46-57%, guard at 98%) |
| Parser fix | Job 4997 failed import because "a 10 year enterprise security commitment" was read as a 10-year candidate minimum (also "up to 15 years of security and maintenance commitments"). `experience.COMMITMENT_AFTER` now skips employer promises; **`EVIDENCE_VERSION` is `evidence-v12`**, so all judgments went stale and the 70 saved verdict files were re-imported without re-judging (70 saved, 0 rejected). Suite **871 passed**, Ruff clean |
| State after | 70 current judgments; final ELIGIBLE 4 / INELIGIBLE 1 (550) / UNVERIFIED 65; integrity ok, 0 foreign-key violations, 188 assessments (184 session rows incl. re-imports) |
| Batch 3 | 17 Arbeitnow jobs that are explicitly remote and match the target stack (the other 87 target-stack Arbeitnow jobs have no workplace flag, i.e. city offices, and were held back): 17 saved, 0 skipped. New ELIGIBLE: ElevenLabs #4092; new INELIGIBLE: Tether #2897 (internship for MSc/PhD candidates) and #2900 (Master's degree required); the 14 others UNVERIFIED (mostly Tether AI research roles). 5-hour limit read 13%, weekly 17% afterwards |
| State after batch 3 | 87 jobs judged (of 1,141): ELIGIBLE 5 / INELIGIBLE 3 / UNVERIFIED 79; integrity ok, 0 foreign-key violations, 205 assessments (201 session rows). 1,054 still unjudged: ~538 senior titles, ~87 office-based Arbeitnow target-stack jobs, ~231 other Arbeitnow, 175 German/French postings |

## 2026-11-11 — Dashboard main page redesigned: Focus view (committed 2026-11-16, see the commit block above)

| Item | Result |
|---|---|
| Behavior | `/` is now the Focus page (template 1 chosen by the user): Eligible / Unverified / Ineligible tabs from the Module 3 final decision, search and source filter, expandable job cards, **Mark applied** (tracker status APPLIED) that removes a job from the filters into an **Applied** tab, with **Undo** (back to SAVED). Only judged, active, listed jobs are listed; the count of unjudged jobs links to Job Explorer. Overview moved to `/overview`; other pages are under a **More** menu. New warm-paper / forest-green scheme on every page |
| Code | New `dashboard/focus.py`; `render.py` (nav, layout, CSS), `app.py` (routes); no schema or tracker change, no JavaScript (CSP unchanged) |
| Tests | 4 new Focus tests and 2 updated (`/overview`); full suite **870 passed**, Ruff/`pip check`/`git diff --check` clean. Real database: integrity ok, 0 applications created by testing (the apply flow was exercised on a temporary database in tests only) |
| Limits | Pages show 25 jobs each; the unjudged jobs are not listed on the main page; closed or unlisted jobs are hidden from the filters; narrow screens stack the buttons under each card |

## 2026-11-06 — Module 3 session judgment `judgment-v1`, decision `final-v2` (committed 2026-11-16, see the commit block above)

| Item | Result |
|---|---|
| Behavior | Claude in a local Claude Code session judges one job against the **approved sanitized profile**; no Gemini, no API key, no external request. `match judge-export --job-id N` writes a review packet (numbered job items, profile segments with P-IDs, skills catalog, fixed 15-month experience, untrusted-data notice, verdict template) to ignored `work/judgments/`; the session writes `work/judgments/job-N.json`; `match judge-import --file PATH` validates in Python and only then saves. Invalid files save nothing and list every reason (exit 1) |
| Validation | Job must be in the candidate population (Module 2 INELIGIBLE rejected); job fingerprint and profile hash must match; every requirement ID exactly once; job quote must be inside that item's text and CV quote inside the cited profile segment; skills-list-only evidence cannot be credited; `met` on a "professional/production" item needs `experience[...]` evidence and, for a named technology, evidence that names it; numeric total-experience minimums are decided by Python (contradicting verdict or project/education evidence rejected; the file has no years field); ELIGIBLE needs no unmet mandatory item; INELIGIBLE needs a Python-confirmed experience gap or a quoted unmet hiring condition; a missing skill alone cannot make INELIGIBLE |
| Storage | Additive `ai_assessments` rows only (model `claude-session`, `-2`… for revisions; `assessment_mode` `session_judgment`; own rule hash = base rule hash + `judgment-v1`, so any Module 2/3 version change makes it stale). **No schema change.** A verified `jobs-pre-judgment-*` backup is made before the first such row. Existing assessments, classifications, profiles and history are untouched; the deterministic score is computed from the session's requirement kinds with the production resolver and stored beside the verdicts as the cross-check, with disagreements listed |
| Decision | `decision.py` `final-v2`: hard blockers (current manual restriction; experience gap ≥ 12 months) first, then the judgment combined with the score rules: judgment ELIGIBLE + score rules INELIGIBLE → UNVERIFIED `judgment_conflicts_with_score`; judgment UNVERIFIED + score ceiling < 60 → INELIGIBLE; otherwise the more cautious of the two. A current judgment replaces the Gemini/offline assessment as the row's technical view in `rank`/dashboard; Gemini rows stay in the history |
| INELIGIBLE rule fix (2026-11-11) | `judge-import` now rejects an INELIGIBLE whose blocker is an experience gap below `EXPERIENCE_GAP_INELIGIBLE_MONTHS` (12) or not a total-experience minimum; `decision.hard_blocker` is shared, and `decide` reads an older INELIGIBLE row with such a blocker as UNVERIFIED `judgment_blocker_not_confirmed` (job #22 was INELIGIBLE on a 9-month gap; it now reads UNVERIFIED; the stored row is unchanged). Suite **860 passed** |
| Scoped-years fix (2026-11-11) | Years tied to one skill or area ("Strong Haskell skills with at least 3 years", "3+ years of experience building and operating cloud infrastructure") were read as total experience, so jobs 5205 and 5226 showed INELIGIBLE although the verdict said UNVERIFIED. `experience.scoped_years_text` now marks them scope `technology` at the source: **`EVIDENCE_VERSION` is `evidence-v11`**, `decision` is `final-v3` (also reads old rows by the same rule, and adds a note when scoped years exceed the career). The version bump made all session judgments stale; the 44 valid saved verdict files were re-imported (new revision rows, old rows kept) with no re-judging. `job-22.json` was rejected by the new INELIGIBLE rule and needs re-judging as UNVERIFIED. Now: 44 current judgments, final ELIGIBLE 3 / INELIGIBLE 1 (550) / UNVERIFIED 40, #22 stale. Suite **866 passed**, Ruff/`pip check`/`git diff --check` clean, integrity ok, 0 foreign-key violations |
| Privacy change | The labeler sees the sanitized approved profile (previously only Gemini-side job text). It stays on this computer; the saved row records this in `provenance.privacy` |
| Tests | New `tests/test_judge.py` (26 cases: valid save, read-only export, ranking use, staleness, tampered job, wrong profile, 13 rejection mutations, experience override, project-as-employment, Module 2 INELIGIBLE, manual restriction, prompt-injection line, combination rules, CLI + backup). Full suite **857 passed**; Ruff check/format, `pip check`, `git diff --check` clean. Real database (read-only URI): `integrity_check` ok, 0 foreign-key violations, 4 assessments, 0 session judgments. Dry run: `match judge-export --job-id 4982` produced a 64-item, 175-segment packet (114 KB) outside the repo; nothing was judged or saved on real jobs |
| Limits | Judging is manual, one job at a time; a packet is large (~30k tokens). The labeler's CV-evidence check is textual (quote in segment, technology named), not semantic. `python_experience_comparison` in the packet is shown for any item containing a number of years, including non-experience wording; only items classified `exp` are enforced. Job text over 12,000 characters is refused. Thresholds (12 months, 60) untested against reviewed examples |

## 2026-11-06 — Module 3 final suitability decision `final-v1` (committed 2026-11-16, see the commit block above)

| Item | Result |
|---|---|
| Behavior | New `job_scraper/decision.py`: a derived, never-stored ELIGIBLE / INELIGIBLE / UNVERIFIED *suitability for applying* tag per ranking row. Module 2 INELIGIBLE jobs stay outside the population; Module 3 only tags the rest. Rules, first match wins: current manual `confirmed_restricted` → INELIGIBLE; confirmed total-experience minimum ≥ 12 months above approved (`EXPERIENCE_GAP_INELIGIBLE_MONTHS`, a judgment, not data-derived) → INELIGIBLE; score ceiling < 60 → INELIGIBLE; score ≥ 60 → ELIGIBLE; else UNVERIFIED. No current assessment → no tag. Missing CV skill alone never decides |
| Separation | Pakistan hiring stays the Module 2 status, carried in the decision as `pakistan_hiring`; ELIGIBLE does not mean employer-confirmed hiring |
| Wiring | `ranking.build` adds `final`; `rank list --final`, counts and text output, `rank explain`; dashboard: overview final-decision counts, "Final (M3)" column and filter in Job Explorer, rankings and My Applications, job-detail header with separate "Pakistan hiring (Module 2)" and "Suitability (Module 3)" labels, "Not judged yet" view (`/jobs?final=none`); dashboard tests 18 |
| No migration | No schema change, no assessment or cache invalidation, no Gemini call, profile untouched |
| Tests | New `tests/test_decision.py`; one existing counts assertion updated. Full suite **829 passed**; Ruff check/format, `pip check`, `git diff --check` clean. Real database read with `rank list` only |
| Limits | All current assessments are stale, so every job shows no decision until re-assessed (paid, needs your authorization). Thresholds (12 months, 60) are untested against real reviewed examples |

## 2026-11-04 — Database refresh and Module 2/3 quality improvement (committed 2026-11-16, see the commit block above)

| Item | Result |
|---|---|
| Collection | `collect --all --max-pages 10` (Himalayas and Arbeitnow partial, Remotive exhaustive and closed 1 job) plus new boards Canonical (Greenhouse, 314) and Supabase (Ashby, 69). 2,985 → 5,250 jobs; 0 merged, 0 errors; population 781 → 1,141 |
| Module 2 | `pakistan-0-3-v3.9`: year parsing under headings and context words, time-frame months no longer experience, conditional office rules cannot exclude, student-only roles INELIGIBLE, provider location text ("Home based - Worldwide", "Remote, Global"), "every time zone" and "work from home" wording |
| Module 3 | `requirements-v4` / `evidence-v10`: relevance needs a software-engineering title; ambiguous location policy (GitLab R051) normalizes `nonreq` to `unresolved` with a saved trace; job-board footers are not hiring conditions; Module 3B reads number words and brackets like Module 2; unmarked "X or Y" lists are alternatives, slash lists stay unresolved. Prompt, schema, scoring unchanged; rule hash `d3840f80db715d79` |
| Cache | assessments #1–#4 are stale and preserved; offline rescore cannot refresh them (compatibility contract); no replacement was generated |
| Tests | **815 passed** (695 + 120); Ruff, `pip check`, `git diff --check` clean; integrity ok, foreign keys 0; no Gemini request |
| Reports | `work/DATABASE_REFRESH_AND_SOURCE_AUDIT.md`, `work/MODULE_2_3_QUALITY_IMPROVEMENT.md`, `work/PROMISING_REMOTE_ENGINEERING_JOBS.md` |

## 2026-11-04 — Module 4 local dashboard and application tracking (committed with `92b1d2a`, tag `module-4-dashboard-application-tracking`)

| Item | Result |
|---|---|
| Baseline | `main` at `f2cf099` (clean) before work; code committed as `92b1d2a`; documentation committed separately |
| Added | `job_scraper/tracking.py`, `job_scraper/dashboard/` (`data.py`, `render.py`, `app.py`), CLI `dashboard`, `tests/test_tracking.py`, `tests/test_dashboard.py` |
| Modified | `job_scraper/cli.py` (new command), `job_scraper/ranking.py` (optional `population=` argument only), `job_scraper/storage.py` (one call that re-points an application when `upsert` merges duplicate jobs) |
| Database | additive tables `job_applications`, `job_application_history`, `app_tracking_meta`; verified backups `jobs-pre-module4-tracking-20261104T121408Z` and an automatic `jobs-pre-tracking-*`; migration tested on a copy first; `user_version` still 2; integrity ok, foreign keys 0 |
| Tests | Full suite **695 passed** (658 + 37 new), re-run before the commit; Ruff check/format, `pip check`, `git diff --check` clean; real database integrity ok, foreign keys 0, 0 application records (none created by testing) |
| Smoke | Browser pane: overview, explorer search/filters, rankings, job detail, save job and status change on a database copy; HTTP checks of all pages against the real database (read-only, 54–85 ms warm) |
| Limits | No assessments are triggered from the UI (CLI only); first load builds the cache (~7 s); filtering runs in memory over lightweight rows (fine for tens of thousands of jobs); `signature()` change detection costs ~50 ms per request; Module 3 limitations unchanged |

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
| Module 2 screening | Implemented in `screening.py`/`processing.py`, with extensive regressions. Current rule version `pakistan-0-3-v3.9`. |
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
