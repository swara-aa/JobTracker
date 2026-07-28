from job_agent.web import create_app
from job_agent.public_enrichment import start_overnight_public_backfill


app = create_app()


if __name__ == "__main__":
    # The reloader creates a second process and can retain stale environment values.
    start_overnight_public_backfill()
    app.run(debug=False, use_reloader=False)
