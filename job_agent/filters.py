from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

from job_agent import config
from job_agent.classification import ROLE_FAMILIES


USA_STATE_CODES = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def posted_within_last_7_days(posted_at: datetime) -> bool:
    return posted_at >= utc_now() - timedelta(days=7)


def looks_like_usa_location(location: str) -> bool:
    normalized = normalize_text(location)
    if any(keyword in normalized for keyword in config.USA_LOCATION_KEYWORDS):
        return True

    parts = [part.strip() for part in location.split(",") if part.strip()]
    if len(parts) >= 2 and parts[-1].upper() in USA_STATE_CODES:
        return True

    return False


def is_entry_level(title: str) -> bool:
    normalized = normalize_text(title)
    if any(keyword in normalized for keyword in config.ENTRY_LEVEL_NEGATIVE_KEYWORDS):
        return False
    return any(keyword in normalized for keyword in config.ENTRY_LEVEL_POSITIVE_KEYWORDS)


def matches_role_query(title: str, role_query: str, description: str = "") -> bool:
    normalized_title = normalize_text(title)
    normalized_text = normalize_text(f"{title} {description[:5000]}")
    normalized_query = normalize_text(role_query)

    family_keywords = {
        normalize_text(label): keywords
        for label, keywords in ROLE_FAMILIES
    }
    if normalized_query in family_keywords:
        return any(keyword in normalized_text for keyword in family_keywords[normalized_query])

    if normalized_query in {"software engineer", "software engineering"}:
        return any(
            phrase in normalized_text
            for phrase in [
                "software engineer",
                "software developer",
                "frontend",
                "front end",
                "backend",
                "back end",
                "full stack",
            ]
        )

    if normalized_query == "ai/ml engineer":
        return any(
            phrase in normalized_title
            for phrase in [
                "ai engineer",
                "ml engineer",
                "machine learning engineer",
                "artificial intelligence engineer",
                "ai/ml engineer",
            ]
        )

    return normalized_query in normalized_text


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())
