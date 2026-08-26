from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from job_agent.config import DB_PATH, get_user_setting
from job_agent.gemini_analysis import API_URL, DEFAULT_MODEL, _post_with_retries, fetch_public_job_description
from job_agent.storage import ensure_database, fetch_job, fetch_resumes, save_application_helper


MAX_DESCRIPTION_LENGTH = 18000
MAX_RESUME_LENGTH = 18000
HELPER_SCHEMA = {
    "type": "object",
    "properties": {
        "resume_emphasis": {"type": "array", "items": {"type": "string"}},
        "keywords_to_weave": {"type": "array", "items": {"type": "string"}},
        "gaps_to_handle": {"type": "array", "items": {"type": "string"}},
        "tailoring_steps": {"type": "array", "items": {"type": "string"}},
        "cover_letter": {"type": "string"},
    },
    "required": [
        "resume_emphasis",
        "keywords_to_weave",
        "gaps_to_handle",
        "tailoring_steps",
        "cover_letter",
    ],
}


def generate_application_helper(job_id: int, resume_id: int) -> dict[str, Any]:
    api_key = get_user_setting("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")

    job = fetch_job(job_id)
    if job is None:
        raise ValueError("Job not found.")
    resume = next((item for item in fetch_resumes() if int(item["id"]) == resume_id), None)
    if resume is None:
        raise ValueError("The selected resume no longer exists.")

    description = str(job.get("description") or "").strip()
    if not description:
        description = fetch_public_job_description(str(job["link"]))
        if not description:
            raise RuntimeError("The public job description could not be retrieved.")
        _save_description(job_id, description)

    model = get_user_setting("GEMINI_MODEL") or DEFAULT_MODEL
    prompt = f"""
Create a truthful, personalized application helper for one job and one resume.

JOB: {job['title']} at {job['company']}
LOCATION: {job['location']}
JOB DESCRIPTION:
{description[:MAX_DESCRIPTION_LENGTH]}

SELECTED RESUME: {resume['name']}
RESUME TEXT:
{str(resume['content'])[:MAX_RESUME_LENGTH]}

Return JSON with:
- resume_emphasis: 3 to 5 concise facts, projects, skills, or outcomes already supported by this resume that should be prominent.
- keywords_to_weave: 4 to 8 exact terms from the posting that the resume truthfully supports and should use where natural.
- gaps_to_handle: 2 to 4 important posting requirements not clearly evidenced. Say "No material gap identified" if appropriate.
- tailoring_steps: 3 to 5 concrete, truthful edits to the selected resume before applying. Never instruct the candidate to invent skills, metrics, responsibilities, or experience.
- cover_letter: a polished 180 to 260 word cover letter addressed to "Hiring Team". It must use only evidence from the resume, connect it to this exact job, avoid placeholders, and acknowledge no unsupported qualification. Do not make claims about visa eligibility. Do not use markdown.

Scores estimate alignment, not interview likelihood. Keep each list item practical and concise.
""".strip()
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": HELPER_SCHEMA,
            "maxOutputTokens": 2800,
        },
    }
    response = _post_with_retries(
        API_URL.format(model=model),
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json=payload,
        timeout=180,
    )
    body = response.json()
    parts = body.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    response_text = next((part.get("text") for part in parts if part.get("text")), "")
    if not response_text:
        raise RuntimeError(f"Gemini returned no application helper: {json.dumps(body)[:500]}")
    helper = _parse_helper_response(response_text)
    save_application_helper(
        job_id=job_id,
        resume_id=resume_id,
        content=helper,
        model=model,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    return helper


def _parse_helper_response(response_text: str) -> dict[str, Any]:
    helper = json.loads(response_text)
    if not isinstance(helper, dict):
        raise ValueError("Gemini returned an invalid application helper.")
    for field in ("resume_emphasis", "keywords_to_weave", "gaps_to_handle", "tailoring_steps"):
        values = helper.get(field)
        if not isinstance(values, list) or not values or not all(
            isinstance(value, str) and value.strip() for value in values
        ):
            raise ValueError("Gemini returned incomplete application-helper guidance.")
        helper[field] = [value.strip() for value in values][:8]
    cover_letter = helper.get("cover_letter")
    if not isinstance(cover_letter, str) or not cover_letter.strip():
        raise ValueError("Gemini returned an incomplete cover letter.")
    helper["cover_letter"] = cover_letter.strip()
    return helper


def _save_description(job_id: int, description: str) -> None:
    ensure_database()
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("UPDATE jobs SET description = ? WHERE id = ?", (description, job_id))
        connection.commit()
