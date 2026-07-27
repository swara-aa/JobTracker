# Privacy

JobTracker is designed to be local-first.

## Stored Locally

- Job records and application tracking data in `data/jobs.db`
- Resume text extracted from uploads
- Local scoring results, notes, and follow-up dates

## External Requests

- Public job pages are requested only when you start collection or background description capture.
- Gemini receives job and resume text only when you explicitly request Gemini analysis or resume comparison.
- Optional SMTP digest sends the digest to the configured recipient.

## Repository Safety

The repository must never include databases, resumes, API keys, SMTP credentials, browser profiles, or captured private data. Review `git status` before every push.
