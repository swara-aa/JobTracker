import os

from job_agent.web import create_app
from job_agent.automation import start_automation_coordinator


app = create_app()


automation_enabled = os.getenv("JOBTRACKER_AUTOMATION_ENABLED", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

if __name__ == "__main__" or automation_enabled:
    # The reloader creates a second process and can retain stale environment values.
    start_automation_coordinator()


if __name__ == "__main__":
    app.run(debug=False, use_reloader=False)
