from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any

from job_agent.config import DB_PATH, get_user_setting
from job_agent.gemini_analysis import DEFAULT_MODEL, _post_with_retries, API_URL, fetch_public_job_description
from job_agent.storage import ensure_database, fetch_job, fetch_resumes


MATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "rankings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "resume_id": {"type": "integer"},
                    "score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "rationale": {"type": "string"},
                    "matched_skills": {"type": "array", "items": {"type": "string"}},
                    "missing_skills": {"type": "array", "items": {"type": "string"}},
                    "improvements": {"type": "array", "items": {"type": "string"}},
                    "hard_no": {"type": "boolean"},
                    "hard_no_reasons": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "resume_id",
                    "score",
                    "rationale",
                    "matched_skills",
                    "missing_skills",
                    "improvements",
                    "hard_no",
                    "hard_no_reasons",
                ],
            },
        }
    },
    "required": ["rankings"],
}

MAX_ADVICE_SELECTION_LENGTH = 2000
MAX_ADVICE_DESCRIPTION_LENGTH = 16000
MAX_ADVICE_RESUME_LENGTH = 30000
MAX_MATCH_DESCRIPTION_LENGTH = 18000
MAX_MATCH_RESUME_LENGTH = 14000
MAX_ADVICE_ATTEMPTS = 2

ADVICE_SCHEMA = {
    "type": "object",
    "properties": {
        "assessment": {"type": "string"},
        "where_to_add": {"type": "string"},
        "truthful_rewrite": {"type": "string"},
        "next_step": {"type": "string"},
    },
    "required": ["assessment", "where_to_add", "truthful_rewrite", "next_step"],
}


def compare_resumes(job_id: int) -> list[dict[str, Any]]:
    api_key = get_user_setting("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")

    job = fetch_job(job_id)
    if job is None:
        raise ValueError(f"Job {job_id} does not exist.")
    resumes = fetch_resumes()
    if not resumes:
        raise ValueError("Upload at least one resume before comparing.")

    description = str(job.get("description") or "").strip()
    if not description:
        description = fetch_public_job_description(str(job["link"]))
        if not description:
            raise RuntimeError("The public job description could not be retrieved.")
        _save_job_description(job_id, description)

    model = get_user_setting("GEMINI_MODEL") or DEFAULT_MODEL
    resume_sections = "\n\n".join(
        f"=== RESUME ID {resume['id']}: {resume['name']} ===\n{str(resume['content'])[:MAX_MATCH_RESUME_LENGTH]}"
        for resume in resumes
    )
    prompt = f"""
Compare each resume with this job posting and rank its application fit.

JOB: {job['title']} at {job['company']}
LOCATION: {job['location']}
JOB DESCRIPTION:
{description[:MAX_MATCH_DESCRIPTION_LENGTH]}

RESUMES:
{resume_sections}

Return exactly one ranking for every supplied resume ID. Score each resume from 0 to 100 using
the same evidence-based rubric: required skills 35 points, relevant experience/projects 30,
education and baseline qualifications 15, preferred skills 10, and resume clarity/evidence 10.
Do not reward keyword repetition without supporting experience. Do not invent qualifications.
Missing skills must mean skills requested by this posting but not evidenced in that resume.
For matched_skills and missing_skills, use the posting's exact skill, tool, technology, and
requirement terms wherever possible. Matched terms must be supported by the resume; missing
terms must be absent or not made prominent in the resume.
Improvements must be truthful, specific edits or learning/project suggestions; never advise the
candidate to claim experience they do not have. Keep each item concise.
Set hard_no=true only for an explicit posting requirement that the F-1/OPT candidate cannot
meet, such as U.S. citizenship, a required active security clearance, or explicit language that
the candidate must be permanently authorized to work in the U.S. without present or future
sponsorship. Do not treat ordinary missing skills, degree preferences, or unclear visa language
as a hard no. When hard_no=true, set score=0 and list the exact posting requirement in
hard_no_reasons.
""".strip()
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": MATCH_SCHEMA,
            "maxOutputTokens": 2500,
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
    text = next((part.get("text") for part in parts if part.get("text")), "")
    if not text:
        raise RuntimeError(f"Gemini returned no resume comparison: {json.dumps(body)[:500]}")
    rankings = json.loads(text).get("rankings", [])
    _validate_rankings(rankings, {int(resume["id"]) for resume in resumes})
    _save_rankings(job_id, rankings)
    return rankings


def advise_resume_implementation(
    job_id: int,
    resume_id: int,
    selected_improvement: str,
) -> dict[str, str]:
    api_key = get_user_setting("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")

    selected_improvement = selected_improvement.strip()
    if not selected_improvement:
        raise ValueError("Select an improvement to ask about.")
    if len(selected_improvement) > MAX_ADVICE_SELECTION_LENGTH:
        raise ValueError("Select a shorter part of the improvement.")

    job = fetch_job(job_id)
    if job is None:
        raise ValueError(f"Job {job_id} does not exist.")
    resume = next(
        (item for item in fetch_resumes() if int(item["id"]) == resume_id),
        None,
    )
    if resume is None:
        raise ValueError("The selected resume no longer exists.")

    description = str(job.get("description") or "").strip()
    if not description:
        description = fetch_public_job_description(str(job["link"]))
        if description:
            _save_job_description(job_id, description)

    model = get_user_setting("GEMINI_MODEL") or DEFAULT_MODEL
    prompt = f"""
Help a candidate implement one selected resume-improvement recommendation truthfully.

JOB: {job['title']} at {job['company']}
JOB DESCRIPTION:
{description[:MAX_ADVICE_DESCRIPTION_LENGTH] or 'No description text was available.'}

SELECTED RESUME: {resume['name']}
RESUME TEXT:
{str(resume['content'])[:MAX_ADVICE_RESUME_LENGTH]}

SELECTED IMPROVEMENT:
{selected_improvement}

Return JSON with assessment, where_to_add, truthful_rewrite, and next_step. Every value must be
plain English with one to three complete sentences and no markdown. Keep the entire response
under 320 words. Base the answer only on
evidence in this resume. State whether the candidate already has relevant evidence, then explain
exactly where and how to present it (for example, a specific existing role, project, or skills
section). Give one concise truthful rewrite only if the resume supports it; otherwise state that
no truthful rewrite is available. If evidence is absent, suggest a learning or project step.
Never invent skills, metrics, responsibilities, or experience. Do not imply a resume change
guarantees an interview.
""".strip()
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": ADVICE_SCHEMA,
            "maxOutputTokens": 1400,
        },
    }
    last_error: ValueError | None = None
    for _ in range(MAX_ADVICE_ATTEMPTS):
        response = _post_with_retries(
            API_URL.format(model=model),
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
        body = response.json()
        parts = body.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        advice_text = next((part.get("text") for part in parts if part.get("text")), "").strip()
        if not advice_text:
            raise RuntimeError(f"Gemini returned no resume advice: {json.dumps(body)[:500]}")
        try:
            return _parse_advice_response(advice_text)
        except ValueError as exc:
            last_error = exc
    raise RuntimeError("Gemini returned incomplete resume advice. Please try the button again.") from last_error


def _parse_advice_response(advice_text: str) -> dict[str, str]:
    advice = json.loads(advice_text)
    if not isinstance(advice, dict) or any(
        not isinstance(advice.get(key), str) or not advice[key].strip()
        for key in ADVICE_SCHEMA["required"]
    ):
        raise ValueError("Gemini returned invalid resume advice.")
    return {key: str(advice[key]).strip() for key in ADVICE_SCHEMA["required"]}


def _validate_rankings(rankings: list[dict[str, Any]], expected_ids: set[int]) -> None:
    received_ids = {int(item.get("resume_id", -1)) for item in rankings}
    if received_ids != expected_ids or len(rankings) != len(expected_ids):
        raise ValueError("Gemini did not return exactly one result for every resume.")
    for item in rankings:
        score = item.get("score")
        if not isinstance(score, int) or not 0 <= score <= 100:
            raise ValueError("Gemini returned an invalid resume match score.")
        if not isinstance(item.get("hard_no"), bool):
            raise ValueError("Gemini returned an invalid hard-no assessment.")
        if not isinstance(item.get("hard_no_reasons"), list):
            raise ValueError("Gemini returned invalid hard-no reasons.")
        if item["hard_no"] and score != 0:
            raise ValueError("A hard-no resume match must have a score of zero.")


def _save_job_description(job_id: int, description: str) -> None:
    ensure_database()
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("UPDATE jobs SET description = ? WHERE id = ?", (description, job_id))
        connection.commit()


def _save_rankings(job_id: int, rankings: list[dict[str, Any]]) -> None:
    best_id = max(rankings, key=lambda item: int(item["score"]))["resume_id"]
    analyzed_at = datetime.now(timezone.utc).isoformat()
    ensure_database()
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("DELETE FROM resume_job_matches WHERE job_id = ?", (job_id,))
        for item in rankings:
            connection.execute(
                """
                INSERT INTO resume_job_matches (
                    job_id, resume_id, score, is_best, rationale, matched_skills,
                    missing_skills, improvements, hard_no, hard_no_reasons, analyzed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    int(item["resume_id"]),
                    int(item["score"]),
                    int(int(item["resume_id"]) == int(best_id)),
                    str(item["rationale"]),
                    json.dumps(item["matched_skills"]),
                    json.dumps(item["missing_skills"]),
                    json.dumps(item["improvements"]),
                    int(item["hard_no"]),
                    json.dumps(item["hard_no_reasons"]),
                    analyzed_at,
                ),
            )
        connection.commit()
