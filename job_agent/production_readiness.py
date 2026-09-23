from __future__ import annotations

from datetime import datetime
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from job_agent import config
from job_agent.automation import automation_status
from job_agent.database import backend_name
from job_agent.digest import active_digest_subscribers
from job_agent.storage import today_scoring_summary


def production_readiness_status() -> dict[str, object]:
    now = _local_now()
    today = now.date().isoformat()
    automation = automation_status()
    scoring = today_scoring_summary(today)
    subscribers = active_digest_subscribers()
    public_source_count = sum(
        bool(value.strip())
        for value in (
            config.GREENHOUSE_BOARDS,
            config.LEVER_SITES,
            config.WORKDAY_SITES,
        )
    )
    smtp_configured = bool(
        config.SMTP_HOST
        and config.SMTP_USERNAME
        and config.SMTP_PASSWORD
    )
    collected = int(scoring.get("collected") or 0)
    described = int(scoring.get("described") or 0)
    locally_scored = int(scoring.get("locally_scored") or 0)
    gemini_scored = int(scoring.get("gemini_scored") or 0)
    digest_due = _time_reached(now, config.DIGEST_SEND_TIME)
    checks = [
        _check("postgresql", "PostgreSQL persistence", backend_name() == "postgresql", True, backend_name()),
        _check(
            "password_gate",
            "Password protection",
            _enabled("JOBTRACKER_AUTH_REQUIRED") and bool(config.get_user_setting("JOBTRACKER_ACCESS_PASSWORD")),
            True,
            "enabled" if _enabled("JOBTRACKER_AUTH_REQUIRED") else "disabled",
        ),
        _check(
            "extension_auth",
            "Extension import authentication",
            bool(config.get_user_setting("JOBTRACKER_EXTENSION_IMPORT_TOKEN")),
            True,
            "token configured" if config.get_user_setting("JOBTRACKER_EXTENSION_IMPORT_TOKEN") else "token missing",
        ),
        _check(
            "automation_worker",
            "Azure automation worker",
            bool(automation.get("running")),
            True,
            str(automation.get("worker_heartbeat_at") or "no recent heartbeat"),
        ),
        _check(
            "public_sources",
            "Primary-source collection",
            public_source_count > 0,
            True,
            f"{public_source_count} source type(s) configured",
        ),
        _check(
            "daily_collection",
            "Today's public collection",
            automation.get("last_public_collection_date") == today,
            True,
            str(automation.get("last_public_collection_date") or "not completed"),
        ),
        _check(
            "recent_jobs",
            "Recent jobs discovered",
            int(scoring.get("source_posted_today") or 0) > 0,
            False,
            f"{int(scoring.get('source_posted_today') or 0)} source-posted today",
        ),
        _check(
            "descriptions",
            "Description capture",
            collected == 0 or described == collected,
            True,
            f"{described}/{collected} collected jobs described",
        ),
        _check(
            "local_scoring",
            "Local fallback scoring",
            described == 0 or locally_scored >= described,
            True,
            f"{locally_scored}/{described} described jobs locally scored",
        ),
        _check(
            "gemini_scoring",
            "Gemini scoring",
            described == 0 or gemini_scored > 0,
            True,
            f"{gemini_scored}/{described} described jobs Gemini scored today",
        ),
        _check(
            "email_delivery",
            "Subscriber email configuration",
            smtp_configured and bool(subscribers),
            True,
            f"SMTP {'configured' if smtp_configured else 'missing'}; {len(subscribers)} active subscriber(s)",
        ),
        _check(
            "daily_digest",
            "Today's subscriber digest",
            not digest_due or automation.get("last_digest_date") == today,
            False,
            str(automation.get("last_digest_date") or "not sent today"),
        ),
    ]
    blocking = [check for check in checks if check["critical"] and not check["passed"]]
    return {
        "ready": not blocking,
        "checked_at": now.isoformat(),
        "blocking_count": len(blocking),
        "checks": checks,
    }


def _check(
    check_id: str,
    label: str,
    passed: bool,
    critical: bool,
    detail: str,
) -> dict[str, object]:
    return {
        "id": check_id,
        "label": label,
        "passed": bool(passed),
        "critical": critical,
        "detail": detail,
    }


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _local_now() -> datetime:
    try:
        return datetime.now(ZoneInfo(config.DIGEST_TIMEZONE))
    except ZoneInfoNotFoundError:
        return datetime.now().astimezone()


def _time_reached(now: datetime, value: str) -> bool:
    try:
        hour, minute = value.split(":", 1)
        return (now.hour, now.minute) >= (int(hour), int(minute))
    except (AttributeError, TypeError, ValueError):
        return True
