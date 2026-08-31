from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


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

_data_dir = Path(os.getenv("JOBTRACKER_DATA_DIR", "data"))
DATA_DIR = _data_dir if _data_dir.is_absolute() else BASE_DIR / _data_dir
DB_PATH = DATA_DIR / "jobs.db"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()


def database_backend() -> str:
    """Return the configured persistence backend without opening a connection."""
    if not DATABASE_URL:
        return "sqlite"
    if DATABASE_URL.startswith(("postgres://", "postgresql://")):
        return "postgresql"
    raise ValueError("DATABASE_URL must use a postgresql:// or postgres:// URL.")

_company_database_path = Path(os.getenv("COMPANY_DATABASE_PATH", "config/companies.csv"))
COMPANY_DATABASE_PATH = (
    _company_database_path
    if _company_database_path.is_absolute()
    else BASE_DIR / _company_database_path
)
COMPANY_INTELLIGENCE_ENABLED = os.getenv("COMPANY_INTELLIGENCE_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

DEFAULT_SCHEDULE_TIME = os.getenv("JOB_AGENT_SCHEDULE_TIME", "09:00")
AUTOMATION_PUBLIC_COLLECTION_TIME = os.getenv(
    "JOB_AGENT_PUBLIC_COLLECTION_TIME",
    "07:56",
)
REQUEST_TIMEOUT_SECONDS = int(os.getenv("JOB_AGENT_TIMEOUT_SECONDS", "20"))
GREENHOUSE_BOARDS = os.getenv("JOB_AGENT_GREENHOUSE_BOARDS", "")
LEVER_SITES = os.getenv("JOB_AGENT_LEVER_SITES", "")
SMTP_HOST = os.getenv("JOB_AGENT_SMTP_HOST", "")
SMTP_PORT = int(os.getenv("JOB_AGENT_SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("JOB_AGENT_SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("JOB_AGENT_SMTP_PASSWORD", "")
SMTP_TO = os.getenv("JOB_AGENT_SMTP_TO", "")
DIGEST_SEND_TIME = os.getenv("JOBTRACKER_DIGEST_SEND_TIME", "08:00")
DIGEST_TIMEZONE = os.getenv("JOBTRACKER_DIGEST_TIMEZONE", "America/Chicago")
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

def _csv_values(value: str, fallback: list[str]) -> list[str]:
    values = [item.strip() for item in value.split(",") if item.strip()]
    return values or fallback


ROLE_QUERIES = _csv_values(
    os.getenv("JOB_AGENT_ROLE_QUERIES", ""),
    [
        "Software Engineer",
        "AI/ML Engineer",
    ],
)

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
