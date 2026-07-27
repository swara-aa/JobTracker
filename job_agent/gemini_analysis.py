from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import random
import time
from typing import Any

import requests
from bs4 import BeautifulSoup

from job_agent.config import DB_PATH, USER_AGENT, get_user_setting
from job_agent.storage import ensure_database, fetch_job


DEFAULT_MODEL = "gemini-3.5-flash"
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MAX_RETRY_ATTEMPTS = 5
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "page_access": {
            "type": "string",
            "enum": ["accessible", "unavailable", "insufficient_content"],
        },
        "summary": {"type": "string"},
        "skills_required": {"type": "array", "items": {"type": "string"}},
        "skills_preferred": {"type": "array", "items": {"type": "string"}},
        "responsibilities": {"type": "array", "items": {"type": "string"}},
        "requirements": {"type": "array", "items": {"type": "string"}},
        "education": {"type": "string"},
        "experience": {"type": "string"},
        "location": {"type": "string"},
        "workplace_type": {"type": "string"},
        "employment_type": {"type": "string"},
        "visa": {
            "type": "object",
            "properties": {
                "posting_language": {
                    "type": "string",
                    "enum": [
                        "explicit_sponsorship",
                        "explicit_no_sponsorship",
                        "work_authorization_required",
                        "citizenship_required",
                        "not_mentioned",
                    ],
                },
                "f1_opt_compatibility": {
                    "type": "string",
                    "enum": ["likely_compatible", "likely_not_compatible", "unclear"],
                },
                "future_sponsorship": {
                    "type": "string",
                    "enum": ["offered", "not_offered", "unclear"],
                },
                "evidence": {"type": "string"},
            },
            "required": [
                "posting_language",
                "f1_opt_compatibility",
                "future_sponsorship",
                "evidence",
            ],
        },
    },
    "required": [
        "page_access",
        "summary",
        "skills_required",
        "skills_preferred",
        "responsibilities",
        "requirements",
        "education",
        "experience",
        "location",
        "workplace_type",
        "employment_type",
        "visa",
    ],
}


def analyze_job(job_id: int) -> dict[str, Any]:
    job = fetch_job(job_id)
    if job is None:
        raise ValueError(f"Job {job_id} does not exist.")

    api_key = get_user_setting("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")

    model = get_user_setting("GEMINI_MODEL") or DEFAULT_MODEL
    _set_running(job_id, model)
    try:
        description = str(job.get("description") or "").strip()
        if not description:
            try:
                description = fetch_public_job_description(str(job["link"]))
            except requests.RequestException:
                description = ""
            if description:
                _save_description(job_id, description)
        result = request_analysis(
            str(job["link"]),
            api_key=api_key,
            model=model,
            job=job,
            description=description,
        )
        _save_result(job_id, model, result)
        return result
    except Exception as exc:
        _save_error(job_id, model, str(exc))
        raise


def fetch_public_job_details(link: str) -> dict[str, object]:
    response = requests.get(
        link,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        timeout=30,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    metadata: dict[str, object] = {}
    description_text = ""
    description = soup.select_one(
        ".show-more-less-html__markup, .description__text, section.description"
    )
    if description:
        description_text = description.get_text("\n", strip=True)[:50000]

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        entries = data if isinstance(data, list) else [data]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_type = entry.get("@type")
            is_job_posting = entry_type == "JobPosting" or (
                isinstance(entry_type, list) and "JobPosting" in entry_type
            )
            if not is_job_posting and not entry.get("title"):
                continue
            if not description_text and entry.get("description"):
                fragment = BeautifulSoup(str(entry["description"]), "html.parser")
                description_text = fragment.get_text("\n", strip=True)[:50000]
            organization = entry.get("hiringOrganization")
            location = entry.get("jobLocation")
            metadata = {
                "title": entry.get("title", ""),
                "company": organization.get("name", "") if isinstance(organization, dict) else "",
                "date_posted": entry.get("datePosted", ""),
                "valid_through": entry.get("validThrough", ""),
                "employment_type": entry.get("employmentType", ""),
                "location": _public_location(location),
                "salary": entry.get("baseSalary", ""),
            }
            break
    return {"description": description_text, "metadata": metadata}


def fetch_public_job_description(link: str) -> str:
    return str(fetch_public_job_details(link)["description"])


def _public_location(value: object) -> str:
    locations = value if isinstance(value, list) else [value]
    labels: list[str] = []
    for location in locations:
        address = location.get("address", {}) if isinstance(location, dict) else {}
        if isinstance(address, dict):
            label = ", ".join(
                str(address.get(part) or "").strip()
                for part in ("addressLocality", "addressRegion", "addressCountry")
                if str(address.get(part) or "").strip()
            )
            if label:
                labels.append(label)
    return " | ".join(labels)


def request_analysis(
    link: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    job: dict[str, Any] | None = None,
    description: str = "",
) -> dict[str, Any]:
    job = job or {}
    posting_text = description or "No description text was available; use URL Context."
    prompt = f"""
Analyze the public job posting at this URL: {link}

Job title: {job.get("title", "")}
Company: {job.get("company", "")}
Captured location: {job.get("location", "")}

The application fetched this posting text directly from the public page:
---
{posting_text}
---

Treat the supplied posting text as the primary source. Use URL Context only to supplement it.
Extract only facts supported by the supplied text or page. If posting text is supplied, set
page_access=accessible and provide a useful summary, skills, responsibilities, and requirements.
Do not infer visa sponsorship from the employer's reputation or general history.
For visa.evidence, summarize the exact work-authorization or sponsorship language;
if the posting says nothing, use posting_language=not_mentioned and state that it is not mentioned.
F-1 OPT compatibility is not legal advice: mark unclear unless the posting language clearly
supports or conflicts with a candidate who currently has OPT work authorization.
Return concise structured data matching the response schema.
""".strip()
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"urlContext": {}}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": ANALYSIS_SCHEMA,
        },
    }
    response = _post_with_retries(
        API_URL.format(model=model),
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    body = response.json()
    parts = body.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = next((part.get("text") for part in parts if part.get("text")), "")
    if not text:
        raise RuntimeError(f"Gemini returned no analysis: {json.dumps(body)[:500]}")
    result = json.loads(text)
    _validate_result(result)
    return result


def _post_with_retries(url: str, **kwargs: Any) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(MAX_RETRY_ATTEMPTS):
        try:
            response = requests.post(url, **kwargs)
            if response.status_code in RETRYABLE_STATUS_CODES:
                last_error = RuntimeError(f"Gemini temporary error HTTP {response.status_code}")
                if attempt < MAX_RETRY_ATTEMPTS - 1:
                    time.sleep(_retry_delay(response, attempt))
                    continue
                break
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt < MAX_RETRY_ATTEMPTS - 1:
                time.sleep(2**attempt + random.uniform(0, 0.5))

    if isinstance(last_error, RuntimeError):
        if "HTTP 429" in str(last_error):
            raise RuntimeError(
                "Gemini rate limit reached. Wait a few minutes, then try the analysis again."
            )
        if "HTTP 503" in str(last_error):
            raise RuntimeError("Gemini is temporarily unavailable. Please try again in a minute.")
    raise RuntimeError(str(last_error or "Gemini request failed."))


def _retry_delay(response: requests.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After", "").strip()
    try:
        return max(0.0, min(float(retry_after), 60.0))
    except ValueError:
        return 2**attempt + random.uniform(0, 0.5)


def _validate_result(result: dict[str, Any]) -> None:
    required = set(ANALYSIS_SCHEMA["required"])
    missing = required.difference(result)
    if missing:
        raise ValueError(f"Gemini response is missing: {', '.join(sorted(missing))}")
    if not isinstance(result.get("visa"), dict):
        raise ValueError("Gemini response has an invalid visa assessment.")


def _set_running(job_id: int, model: str) -> None:
    _execute_update(
        "UPDATE jobs SET gemini_status = 'running', gemini_error = '', gemini_model = ? WHERE id = ?",
        (model, job_id),
    )


def _save_result(job_id: int, model: str, result: dict[str, Any]) -> None:
    visa = result["visa"]
    visa_status = " / ".join(
        [
            str(visa["posting_language"]),
            f"F-1 OPT: {visa['f1_opt_compatibility']}",
            f"Future: {visa['future_sponsorship']}",
        ]
    )
    _execute_update(
        """
        UPDATE jobs
        SET gemini_status = 'complete', gemini_summary = ?,
            gemini_skills_required = ?, gemini_skills_preferred = ?,
            gemini_responsibilities = ?, gemini_requirements = ?,
            gemini_education = ?, gemini_experience = ?, gemini_location = ?,
            gemini_workplace_type = ?, gemini_employment_type = ?,
            gemini_visa_status = ?, gemini_visa_evidence = ?, gemini_error = '',
            gemini_model = ?, gemini_analyzed_at = ?
        WHERE id = ?
        """,
        (
            result["summary"],
            json.dumps(result["skills_required"]),
            json.dumps(result["skills_preferred"]),
            json.dumps(result["responsibilities"]),
            json.dumps(result["requirements"]),
            result["education"],
            result["experience"],
            result["location"],
            result["workplace_type"],
            result["employment_type"],
            visa_status,
            visa["evidence"],
            model,
            datetime.now(timezone.utc).isoformat(),
            job_id,
        ),
    )


def _save_error(job_id: int, model: str, error: str) -> None:
    _execute_update(
        """
        UPDATE jobs
        SET gemini_status = 'failed', gemini_error = ?, gemini_model = ?,
            gemini_analyzed_at = ?
        WHERE id = ?
        """,
        (error[:1000], model, datetime.now(timezone.utc).isoformat(), job_id),
    )


def _save_description(job_id: int, description: str) -> None:
    _execute_update(
        "UPDATE jobs SET description = ? WHERE id = ?",
        (description, job_id),
    )


def _execute_update(query: str, parameters: tuple[Any, ...]) -> None:
    import sqlite3

    ensure_database()
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(query, parameters)
        connection.commit()
