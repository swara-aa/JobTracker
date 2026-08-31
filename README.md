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
- Prioritizes the daily Inbox with a freshness boost: `+8` for jobs posted within 24 hours, `+4` for jobs posted 1–3 days ago, and no boost for jobs posted 4–5 days ago. Confirmed Fortune 500 employers receive a separate `+3` ordering bonus; this is not a sponsorship determination.
- Lets you create a per-job, per-resume Application Helper and cover letter on demand. It uses Gemini only after you press the button and never invents qualifications.

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

## Azure Deployment

> **Privacy warning:** Set an access password before uploading real resumes or private job data to Azure. HTTPS alone does not restrict access.

The Azure deployment keeps the working Flask/Jinja application intact:

- **Backend:** Linux Azure App Service, rather than a Function App. Flask routes, file-backed SQLite, and the background automation coordinator require a long-running, single-worker process.
- **Data:** Azure Files is mounted at `/mounts/jobtracker-data`; `jobs.db` and the automation state are stored there instead of the ephemeral App Service filesystem.
- **Frontend:** Azure Static Web Apps hosts a small Vite compatibility entry page built with `VITE_API_URL`. It opens the existing Flask UI without a risky rewrite. A future standalone SPA can replace this shell.

The backend deliberately runs one App Service instance and one Gunicorn worker. SQLite is not safe for scale-out. Move to PostgreSQL and Blob Storage before enabling multiple instances or treating this as a multi-user service.

### Prerequisites

- Azure CLI logged in with `az login`
- PowerShell 7+ (`pwsh`)
- Node.js 20+ and npm (for the Static Web Apps shell)
- A globally unique `AppName` prefix, for example `swara-jobtracker`
- Optional: a Gemini API key. The script prompts for it only if you pass `-GeminiApiKey`; it never writes the value to the repository.

### First deployment

Run from the project root in PowerShell. The script creates or reuses the resource group, Linux B1 App Service plan, App Service, Azure Storage account, Azure Files share, and Static Web App.

```powershell
./deploy.ps1 `
  -ResourceGroup "jobtracker-rg" `
  -AppName "swara-jobtracker" `
  -Location "eastus"
```

To enable Gemini features, securely enter the key when prompted by PowerShell:

```powershell
$geminiKey = Read-Host "Gemini API key" -AsSecureString
./deploy.ps1 `
  -ResourceGroup "jobtracker-rg" `
  -AppName "swara-jobtracker" `
  -Location "eastus" `
  -GeminiApiKey $geminiKey
```

The backend script generates a cryptographically random `FLASK_SECRET_KEY` when one is not supplied and stores it only in Azure App Service settings. It can also enable the built-in private password gate. This application does not currently use JWT authentication, so the scripts intentionally do **not** create an unused `JWT_SECRET` or `API_SECRET`.

### Protect the cloud workspace

Choose a long unique password, enter it securely, and deploy it with the backend. The password is stored only as an Azure App Service setting; it is not written to the repository.

```powershell
$accessPassword = Read-Host "JobTracker access password" -AsSecureString
./02-backend.ps1 `
  -ResourceGroup "jobtracker-rg" `
  -AppName "swara-jobtracker" `
  -AccessPassword $accessPassword
```

When `JOBTRACKER_AUTH_REQUIRED=true`, every application route requires this password. `/api/health` intentionally remains public so Azure can monitor availability without exposing job or resume data.

### Redeploy one side

```powershell
# Backend code, Azure Files mount, app settings, and /api/health verification
./02-backend.ps1 -ResourceGroup "jobtracker-rg" -AppName "swara-jobtracker"

# Build Vite with the App Service URL, deploy through the SWA deployment token,
# add the exact frontend origin to backend CORS, and verify the frontend URL
./03-frontend.ps1 -ResourceGroup "jobtracker-rg" -AppName "swara-jobtracker"
```

All scripts accept `-BackendMode AppService` (the supported mode) and `-BackendUrl` when using a custom backend hostname. The frontend deployment token is read at deployment time with `az staticwebapp secrets list`; it is never written to a file, source control, or script output.

After deployment, verify:

```powershell
Invoke-RestMethod "https://<app-name>-api.azurewebsites.net/api/health"
```

The full script prints the Static Web Apps URL. Use that URL as the public entry point after adding access control. The LinkedIn extension remains a local-browser helper and should continue to use a backend URL you explicitly configure; do not expose a cloud import endpoint without authentication.

### Production publish

After the Azure resources already exist, use the production wrapper to publish the backend, package the continuous WebJob automation worker, deploy the frontend shell, and run smoke tests:

```powershell
$databaseUrl = Read-Host "PostgreSQL DATABASE_URL" -AsSecureString
$accessPassword = Read-Host "JobTracker access password" -AsSecureString
$importToken = Read-Host "Extension import token" -AsSecureString

./publish-production.ps1 `
  -DatabaseUrl $databaseUrl `
  -AccessPassword $accessPassword `
  -ExtensionImportToken $importToken
```

The default production target is `jobtracker-paid-rg`, `swara-jobtracker-live-api`, and `https://swara-jobtracker-live-api.azurewebsites.net`. Pass `-ResourceGroup`, `-AppName`, `-BackendAppName`, or `-BackendUrl` only when publishing a different environment.

If you omit `-ExtensionImportToken`, the script reuses the existing Azure app setting when present. If none exists, it generates a new token and prints it once so you can paste it into the Chrome extension.

### Production verification

Run smoke tests independently after configuration changes:

```powershell
$accessPassword = Read-Host "JobTracker access password" -AsSecureString
$importToken = Read-Host "Extension import token" -AsSecureString

./scripts/smoke-production.ps1 `
  -BackendUrl "https://swara-jobtracker-live-api.azurewebsites.net" `
  -AccessPassword $accessPassword `
  -ExtensionImportToken $importToken
```

The smoke test checks health, login, dashboard, radar filters, resume library, operations, the LinkedIn import page, and a no-op extension import against the live backend. Add `-FrontendUrl` to verify the Static Web Apps URL in the same run.

### Chrome extension cloud connection

Reload the unpacked extension from `browser_extension`, then open the extension popup:

- Set **Job Tracker URL** to `https://swara-jobtracker-live-api.azurewebsites.net`.
- Paste the **Cloud import token** from Azure or `publish-production.ps1`.
- Click **Save connection**.

The token is stored in Chrome extension local storage, not in this repository. The backend accepts it through an authorization header only for the LinkedIn import APIs; normal app pages still require the regular JobTracker password.

## Optional Configuration

Set only the values you need in your shell environment—never place secrets in source code.

```bash
# Gemini analysis
export GEMINI_API_KEY="your-key"

# Optional local semantic scoring (downloads the MiniLM model once)
export JOB_AGENT_ENABLE_LOCAL_EMBEDDINGS=1

# Optional company intelligence (enabled by default)
export COMPANY_INTELLIGENCE_ENABLED=true
export COMPANY_DATABASE_PATH=config/companies.csv

# Optional public company boards: comma-separated board-or-site-token:Display Name
export JOB_AGENT_ROLE_QUERIES="Software Engineer,AI/ML Engineer,Marketing Coordinator,Financial Analyst"
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

The extension imports visible result cards and advances through result pages after you start it. It waits 10 seconds between pages. Description capture is deliberately deferred until the full collection finishes so LinkedIn pagination and public-page requests do not overlap.

### Daily LinkedIn collection on macOS

1. Reload **Job Tracker Helper** from `chrome://extensions` after updating the project.
2. Open the LinkedIn Jobs search filtered to **Past 24 hours**.
3. Open the extension, choose **Use current LinkedIn search**, select the time and page limit, enable **Run once every morning**, and save.
4. Test once with **Test scheduled collection now**.
5. Install the macOS launcher:

```bash
./scripts/install_macos_daily_automation.sh
```

The installed LaunchAgent owns one Flask process at login and restarts it after an unexpected exit. It also opens Chrome when the service starts so the extension can run its saved search at the configured time. The extension opens the saved search at its configured time and runs at most once successfully per calendar day. If the Mac was asleep at that time, a five-minute extension heartbeat starts the missed collection after the Mac wakes and allows up to three attempts that day. Chrome must remain signed in to LinkedIn. Collection stops safely if the page shows a login, challenge, missing cards, or inaccessible results; it does not bypass access controls.

When the last LinkedIn results page finishes, the extension notifies Flask. The automation coordinator waits two minutes, starts paced public-description capture for jobs still missing descriptions, locally refreshes their skills and visa language, then submits all described unmatched jobs as one Gemini Batch. It checks the batch every five minutes and imports completed scores without requiring the Operations page to be open.

## Daily Collection

```bash
python run_collector.py       # collect once
python run_scheduler.py       # collect once now, then daily
```

Set `JOB_AGENT_SCHEDULE_TIME` (for example `09:00`) for the standalone scheduler. When Flask is kept running by the macOS automation, `JOB_AGENT_PUBLIC_COLLECTION_TIME` (default `07:56`) controls the built-in daily public-board run.

Greenhouse and Lever collection use only the official public board APIs configured above. Each
newly saved board job keeps its API-provided description and gets a local score. Gemini submission
is deferred up to 15 minutes so the public-board and LinkedIn morning collections can be combined
into one batch. You can also run the same workflow from **Operations → Collect Public Boards Now**.

## Company Intelligence

`config/companies.csv` is a small, manually maintained company database. It powers the **Fortune 500 only** browse filter and adds modest local-ranking signals for companies marked as visa-friendly, entry-level hiring, or relevant to software/AI-ML roles. It never rejects an unknown company or overrides job-specific hard-no language.

The CSV header is:

```csv
company_name,aliases,fortune_500,visa_friendly,sponsors_h1b,hires_entry_level,hires_software_engineers,hires_ai_ml,industry,careers_url,notes
```

- Add one company per row. Use `true`, `false`, or leave a boolean blank / use `unknown` when the information is not verified.
- Separate aliases with `|`, for example `Google LLC|Alphabet`. Matching ignores case, punctuation, and suffixes such as `Inc.`, `LLC`, `Ltd`, and `Corporation`.
- The first row for a normalized company name wins; duplicate names or aliases are logged and skipped rather than changing existing metadata unexpectedly.
- This data is an organization-level aid, not proof that a particular job sponsors visas. Keep uncertain fields `unknown` and rely on the job posting for eligibility decisions.

The bundled database includes 2026 Fortune 500 name-only records from [US500's public table](https://us500.com/fortune-500-companies). US500 states that it is not affiliated with Fortune, so treat this as a practical name-match dataset and refresh it periodically. You can refresh that subset while preserving any existing aliases and manual metadata:

```bash
.venv/bin/python scripts/import_fortune_500_names.py
```

The importer intentionally records only `fortune_500=true` for new rows. It leaves visa, H-1B, entry-level, and role-hiring fields unknown until you add a reliable source.

Set `COMPANY_INTELLIGENCE_ENABLED=false` to disable company matching and its ranking signals. Set `COMPANY_DATABASE_PATH` to use a different CSV. If the file is unavailable or malformed, JobTracker logs a warning, starts normally, and treats all companies as unknown.

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
