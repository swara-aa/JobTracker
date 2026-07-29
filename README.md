# JobTracker

Private, local-first job-search workspace for entry-level Software Engineer, AI Engineer, and Machine Learning Engineer roles in the United States.

JobTracker combines public-job collection, resume-aware triage, visa-language review, application tracking, and a local Flask dashboard. Your job database and resume text stay on your computer.

## What It Does

- Collects entry-level US jobs from RemoteOK, We Work Remotely, Remotive, and optional public Greenhouse/Lever boards.
- Imports browser-visible LinkedIn result cards through the included Chrome/Edge extension—no credentials or login automation.
- Captures public job descriptions in a rate-limited local background queue and recalculates local resume-match scores.
- Ranks jobs with local scoring, optional local semantic embeddings, and on-demand Gemini resume comparisons.
- Tracks application pipeline status: `Saved`, `Tailor Resume`, `Applied`, `Interview`, `Rejected`, `Offer`, `Not pursuing`, and `Closed`.
- Separates closed listings and postings that need verification from the active radar without deleting them.
- Shows skill-gap trends and near-miss jobs for periodic review.

## Privacy and Safety

- SQLite data, uploaded resume text, and API keys are never committed to Git.
- Gemini is called only for actions you explicitly request.
- The extension reads browser-visible public job-result cards; it does not handle LinkedIn credentials or bypass access controls.
- Visa results are job-posting analysis, not legal advice. Employer history does not guarantee sponsorship.

See [PRIVACY.md](PRIVACY.md) and [SECURITY.md](SECURITY.md) before deploying or sharing the project.

## Requirements

- Python 3.10+
- Chrome or Edge (optional, for LinkedIn result-card import)
- A Gemini API key (optional, for Gemini analysis/comparisons)

## Setup

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### Windows PowerShell

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Run the Dashboard

```bash
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000). Keep this process running while using the LinkedIn extension or the background description queue.

## Optional Configuration

Set only the values you need in your shell environment—never place secrets in source code.

```bash
# Gemini analysis
export GEMINI_API_KEY="your-key"

# Optional local semantic scoring (downloads the MiniLM model once)
export JOB_AGENT_ENABLE_LOCAL_EMBEDDINGS=1

# Optional public company boards: comma-separated board-or-site-token:Display Name
export JOB_AGENT_GREENHOUSE_BOARDS="board-token:Company Name,another-token:Another Company"
export JOB_AGENT_LEVER_SITES="site-token:Company Name,another-site:Another Company"

# Optional daily SMTP digest
export JOB_AGENT_SMTP_HOST="smtp.example.com"
export JOB_AGENT_SMTP_TO="you@example.com"
```

On Windows, use `$env:NAME="value"` for the current PowerShell session.

## LinkedIn Extension

1. Start the Flask app.
2. Open `chrome://extensions` or `edge://extensions` and enable **Developer mode**.
3. Choose **Load unpacked** and select `browser_extension/`.
4. Open a LinkedIn Jobs results page and start collection from **Job Tracker Helper**.

The extension imports visible result cards and can advance through result pages after you start it. Newly imported jobs are locally pre-scored immediately; public description capture continues in the background.

### Daily LinkedIn collection on macOS

1. Reload **Job Tracker Helper** from `chrome://extensions` after updating the project.
2. Open the LinkedIn Jobs search filtered to **Past 24 hours**.
3. Open the extension, choose **Use current LinkedIn search**, select the time and page limit, enable **Run once every morning**, and save.
4. Test once with **Test scheduled collection now**.
5. Install the macOS launcher:

```bash
./scripts/install_macos_daily_automation.sh
```

The installed LaunchAgent opens Chrome and starts Flask at 7:55 AM. macOS briefly uses Terminal to start the local Flask process because background agents cannot directly read projects stored under Desktop. The extension opens the saved search at its configured time and runs at most once per calendar day. Chrome must remain signed in to LinkedIn. Collection stops safely if the page shows a login, challenge, missing cards, or inaccessible results; it does not bypass access controls.

## Daily Collection

```bash
python run_collector.py       # collect once
python run_scheduler.py       # collect once now, then daily
```

Set `JOB_AGENT_SCHEDULE_TIME` (for example `09:00`) to choose the daily schedule.

Greenhouse and Lever collection use only the official public board APIs configured above. Each
newly saved board job keeps its API-provided description, gets a local score, and is submitted to
Gemini Batch for resume matching when Gemini is configured and no other batch is active. You can
also run the same workflow from **Operations → Collect Public Boards Now**.

## Project Layout

- `app.py` — Flask entry point
- `job_agent/` — collection, storage, scoring, analysis, and web modules
- `browser_extension/` — Chrome/Edge LinkedIn result-card helper
- `data/jobs.db` — local SQLite database (ignored by Git)
- `run_collector.py` / `run_scheduler.py` — collection commands

## Notes

- Public job boards can change their markup or APIs; source adapters may need occasional maintenance.
- Local match scores are alignment signals, not interview predictions.
- Review the **Needs Verification** and **Near-Miss Review** pages periodically instead of treating automation as a final decision-maker.
