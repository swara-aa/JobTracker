from job_agent.web import create_app
from job_agent.public_enrichment import start_overnight_public_backfill
from job_agent.gemini_queue import start_gemini_resume_backlog


app = create_app()


if __name__ == "__main__":
    # The reloader creates a second process and can retain stale environment values.
    start_overnight_public_backfill()
    start_gemini_resume_backlog()
    app.run(debug=False, use_reloader=False)
