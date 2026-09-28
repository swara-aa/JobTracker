# JobTracker AI Requirements and Test Cases

## Functional Requirements

- New users can understand the main dashboard without seeing operational internals first.
- The dashboard shows three clear next steps: upload resume, collect jobs, and review top matches.
- Browse Jobs remains available for deeper filtering by role, location, company, score, date, visa assessment, and pipeline status.
- Dashboard, Browse Jobs, and Settings must use a readable warm academic light theme with comfortable button spacing and no neon-style backgrounds.
- Jobs shown in Dashboard and default Browse Jobs must already have a saved Gemini resume-match score.
- Jobs collected recently but waiting for Gemini must remain visible through the pending Gemini queue.
- New described jobs must be prioritized for immediate Gemini scoring before backlog batch scoring.
- The subscriber digest must only send qualifying matches that pass the configured score threshold and live apply-readiness check.
- Subscriber job recommendations must use Gemini as the score source when `JOBTRACKER_DIGEST_REQUIRE_GEMINI=true`.
- Manual digest sending must use the same Gemini-enabled ranking path as the automatic daily digest.
- Daily digest delivery should wait for morning Gemini scoring until the latest send deadline, then retry when no qualifying matches are available.
- If Gemini credits or credentials are unavailable, subscriber emails must not fall back to local-only scored job recommendations.
- Successful Gemini Batch imports must clear stale billing/retry warnings from Settings.
- Operations must expose production-readiness status, automation status, Gemini scoring progress, public-board collection status, and digest status.

## Nonfunctional Requirements

- The UI must keep a simple parchment, burgundy, and muted-gold palette with accessible contrast and limited visual complexity.
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
- Dashboard, Browse Jobs, and Settings avoid neon lighting effects and keep action controls visually separated.
- Pending queue `/jobs?view=pending&posted_within=24h` exposes locally scored jobs waiting for Gemini.
- Daily email signup route `/digest-signup` returns 200.
- Operations route `/operations` returns 200 and shows scoring/digest status.
- `recent_described_job_ids_without_gemini_match` excludes jobs before the exact cutoff timestamp.
- Automation immediately calls Gemini for recent described jobs while a backlog batch is active.
- Digest waits before the latest send time when today's described jobs are not fully Gemini scored.
- Digest excludes local-only recommendations when Gemini-scored recommendations are required.
- Manual digest sending uses `send_daily_job_digests(use_gemini=True)`.
- Successful batch import clears stale priority Gemini error state.
- Full test suite must pass before production deployment.

## Latest Verification

- `python -m compileall job_agent tests` passed on 2026-09-27.
- `python -m pytest` passed: 81 tests on 2026-09-27.
- Local Flask smoke test on port 5012 returned 200 for `/`, `/jobs`, `/digest-signup`, and `/operations` on 2026-09-27.
- Production smoke test returned 200 after login for `/`, `/jobs`, `/operations`, `/digest-signup`, and `/api/operations/status` on 2026-09-27.
