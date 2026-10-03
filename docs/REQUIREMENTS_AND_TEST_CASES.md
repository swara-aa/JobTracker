# JobTracker AI Requirements and Test Cases

## Functional Requirements

- New users can understand the main dashboard without seeing operational internals first.
- The dashboard shows three clear next steps: upload resume, collect jobs, and review top matches.
- Browse Jobs remains available for deeper filtering by role, location, company, score, date, visa assessment, and pipeline status.
- All user-facing pages must use a cohesive purple/cyan desktop-dashboard theme inspired by the reference design, with readable text and comfortable button spacing.
- Jobs shown in Dashboard and default Browse Jobs must already have a saved Gemini resume-match score.
- Jobs collected recently but waiting for Gemini must remain visible through the pending Gemini queue.
- New described jobs must be prioritized for immediate Gemini scoring before backlog batch scoring.
- The subscriber digest must only send qualifying matches that pass the configured score threshold and live apply-readiness check.
- If no/few verified-open 90+ matches are available, subscriber digest recommendations may fall back to verified-open 80+ and then 70+ matches, with clear lower-score messaging.
- Every subscriber email must include manage-preferences and unsubscribe links.
- Public launch pages must explain what subscribers get, delivery timing, free vs Pro Preview, privacy, and terms before resume/email collection.
- Subscriber role preferences must support both career choices and common majors by mapping majors such as Computer Science, Biology, Finance, Accounting, Marketing, Engineering, and Education into relevant job families.
- Subscriber digest recommendations should prioritize fresher-friendly roles such as internships, co-ops, new-grad, junior, associate, trainee, apprentice, campus, and early-career roles while filtering clearly senior-only postings.
- Subscriber job recommendations must use Gemini as the score source when `JOBTRACKER_DIGEST_REQUIRE_GEMINI=true`.
- Manual digest sending must use the same Gemini-enabled ranking path as the automatic daily digest.
- Daily digest delivery should wait for morning Gemini scoring until the latest send deadline, then retry when no qualifying matches are available.
- If Gemini credits or credentials are unavailable, subscriber emails must not fall back to local-only scored job recommendations.
- Successful Gemini Batch imports must clear stale billing/retry warnings from Settings.
- Operations must expose production-readiness status, automation status, Gemini scoring progress, public-board collection status, and digest status.

## Nonfunctional Requirements

- The UI must keep a deep indigo, lavender, cyan, and magenta palette with accessible contrast and limited visual complexity.
- Buttons and action links must wrap cleanly on smaller screens and avoid crowded pill-style clusters.
- Routine users should not need to understand Azure, WebJobs, batch scoring, or API settings to use the dashboard.
- PostgreSQL support must remain compatible with local SQLite development.
- Automation must avoid tight retry loops when Gemini billing, credentials, or prepaid credits are unavailable.
- Existing local data must not be deleted or overwritten by UI or automation changes.
- Production routes must keep password protection and secure headers/cookie settings enabled when configured.

## Acceptance Test Cases

- Dashboard route `/` returns 200 and presents beginner-oriented actions.
- Browse route `/jobs` returns 200 and defaults to Gemini-scored active jobs.
- Settings route `/operations` returns 200 with readable warm-theme controls.
- Dashboard, Browse Jobs, Settings, Analytics, Resume, Digest Signup, Login, Job Detail, and LinkedIn Import share the same purple/cyan visual system and keep action controls visually separated.
- Pending queue `/jobs?view=pending&posted_within=24h` exposes locally scored jobs waiting for Gemini.
- Daily email signup route `/digest-signup` returns 200.
- Public launch routes `/landing`, `/privacy`, and `/terms` return 200 without login.
- Subscriber manage route `/digest/manage/<token>` is public and allows preference updates for valid tokens.
- Operations route `/operations` returns 200 and shows scoring/digest status.
- Analytics route `/analytics` returns 200 even when a chart category has zero jobs.
- Analytics storage queries must complete before the database connection context closes, matching PostgreSQL behavior.
- Digest matching excludes scores below 70 when 90+/80+ fallback tiers are exhausted.
- Digest email copy explains why 80+ or 70+ matches are included.
- Major-based subscriber preferences expand into relevant career families before matching jobs.
- Senior-only or high-years-required jobs are excluded from fresher-focused subscriber digests when appropriate internship/new-grad/entry-level alternatives exist.
- `recent_described_job_ids_without_gemini_match` excludes jobs before the exact cutoff timestamp.
- Automation immediately calls Gemini for recent described jobs while a backlog batch is active.
- Digest waits before the latest send time when today's described jobs are not fully Gemini scored.
- Digest excludes local-only recommendations when Gemini-scored recommendations are required.
- Manual digest sending uses `send_daily_job_digests(use_gemini=True)`.
- Successful batch import clears stale priority Gemini error state.
- Full test suite must pass before production deployment.

## Latest Verification

- `python -m compileall job_agent tests` passed on 2026-10-03.
- `python -m pytest` passed: 89 tests on 2026-10-03.
- Local route smoke returned 200 for `/landing`, `/digest-signup`, `/privacy`, `/terms`, `/`, `/jobs`, `/operations`, `/analytics`, and `/api/operations/status` on 2026-10-03.
- Local Flask smoke test on port 5012 returned 200 for `/`, `/jobs`, `/digest-signup`, and `/operations` on 2026-09-27.
- Production smoke test returned 200 for public `/landing`, `/privacy`, `/terms`, and `/digest-signup` on 2026-09-28.
- Production smoke test returned 200 after login for `/`, `/jobs`, `/operations`, `/analytics`, and `/api/operations/status` on 2026-09-28; production readiness blocking count was `0`, automation was running, and automation `last_error` was empty.
