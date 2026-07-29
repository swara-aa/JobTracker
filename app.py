from job_agent.web import create_app
from job_agent.automation import start_automation_coordinator


app = create_app()


if __name__ == "__main__":
    # The reloader creates a second process and can retain stale environment values.
    start_automation_coordinator()
    app.run(debug=False, use_reloader=False)
