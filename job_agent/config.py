from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "jobs.db"


def _load_local_env() -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name:
            os.environ.setdefault(name, value)


_load_local_env()

DEFAULT_SCHEDULE_TIME = os.getenv("JOB_AGENT_SCHEDULE_TIME", "09:00")
REQUEST_TIMEOUT_SECONDS = int(os.getenv("JOB_AGENT_TIMEOUT_SECONDS", "20"))
GREENHOUSE_BOARDS = os.getenv("JOB_AGENT_GREENHOUSE_BOARDS", "")
LEVER_SITES = os.getenv("JOB_AGENT_LEVER_SITES", "")
SMTP_HOST = os.getenv("JOB_AGENT_SMTP_HOST", "")
SMTP_PORT = int(os.getenv("JOB_AGENT_SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("JOB_AGENT_SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("JOB_AGENT_SMTP_PASSWORD", "")
SMTP_TO = os.getenv("JOB_AGENT_SMTP_TO", "")
USER_AGENT = os.getenv(
    "JOB_AGENT_USER_AGENT",
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
)


def get_user_setting(name: str) -> str:
    """Read a setting from the process or the persisted Windows user environment."""
    value = os.environ.get(name, "").strip()
    if value or os.name != "nt":
        return value

    # Long-running desktop apps do not automatically inherit environment changes.
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            persisted, _ = winreg.QueryValueEx(key, name)
        return str(persisted).strip()
    except (FileNotFoundError, OSError):
        return ""


def configured_boards(value: str) -> list[tuple[str, str]]:
    boards: list[tuple[str, str]] = []
    for entry in value.split(","):
        token, _, label = entry.strip().partition(":")
        if token:
            boards.append((token.strip(), (label.strip() or token.strip())))
    return boards

ROLE_QUERIES = [
    "Software Engineer",
    "AI/ML Engineer",
]

USA_LOCATION_KEYWORDS = [
    "united states",
    "usa",
    "us",
    "u.s.",
]

ENTRY_LEVEL_POSITIVE_KEYWORDS = [
    "entry level",
    "entry-level",
    "junior",
    "new grad",
    "new graduate",
    "graduate",
    "associate",
    "early career",
    "apprentice",
    "intern",
]

ENTRY_LEVEL_NEGATIVE_KEYWORDS = [
    "senior",
    "staff",
    "principal",
    "lead",
    "manager",
    "director",
    "architect",
    "sr.",
    "sr ",
    "5+ years",
    "7+ years",
    "10+ years",
]
