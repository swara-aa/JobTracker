from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import json
import re
import secrets
import smtplib
import sqlite3
from typing import Any

from job_agent import config
from job_agent.classification import infer_role_family, location_matches
from job_agent.config import DB_PATH, get_user_setting
from job_agent.gemini_analysis import API_URL, DEFAULT_MODEL, _post_with_retries
from job_agent.local_scoring import _score_resume
from job_agent.storage import ensure_database, fetch_jobs


DIGEST_LIMIT = 10
MAX_CANDIDATES = 30
MAX_DIGEST_DESCRIPTION_LENGTH = 5000
MAX_DIGEST_RESUME_LENGTH = 12000

DIGEST_SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "integer"},
                    "score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "rationale": {"type": "string"},
                    "matched_skills": {"type": "array", "items": {"type": "string"}},
                    "missing_skills": {"type": "array", "items": {"type": "string"}},
                    "hard_no": {"type": "boolean"},
                },
                "required": ["job_id", "score", "rationale", "matched_skills", "missing_skills", "hard_no"],
            },
        }
    },
    "required": ["matches"],
}


def subscribe_to_digest(
    *,
    email: str,
    name: str,
    roles: list[str],
    location: str,
    resume_filename: str,
    resume_content: str,
) -> dict[str, object]:
    normalized_email = _normalize_email(email)
    cleaned_roles = [role.strip() for role in roles if role.strip()]
    if not normalized_email:
        raise ValueError("Enter a valid email address.")
    if not cleaned_roles:
        raise ValueError("Choose at least one role or field.")
    if len(resume_content.strip()) < 80:
        raise ValueError("Upload a resume with readable text.")

    ensure_database()
    token = secrets.token_urlsafe(32)
    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.execute(
            """
            INSERT INTO digest_subscribers (
                email, name, roles, location, resume_filename,
                resume_content, active, unsubscribe_token
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(email) DO UPDATE SET
                name = excluded.name,
                roles = excluded.roles,
                location = excluded.location,
                resume_filename = excluded.resume_filename,
                resume_content = excluded.resume_content,
                active = 1
            """,
            (
                normalized_email,
                name.strip(),
                json.dumps(cleaned_roles),
                location.strip(),
                resume_filename.strip(),
                resume_content.strip(),
                token,
            ),
        )
        if cursor.lastrowid:
            subscriber_id = int(cursor.lastrowid)
        else:
            row = connection.execute(
                "SELECT id FROM digest_subscribers WHERE email = ?",
                (normalized_email,),
            ).fetchone()
            subscriber_id = int(row[0])
        connection.commit()
    return {"id": subscriber_id, "email": normalized_email, "roles": cleaned_roles}


def unsubscribe_digest(token: str) -> bool:
    ensure_database()
    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.execute(
            "UPDATE digest_subscribers SET active = 0 WHERE unsubscribe_token = ?",
            (str(token or "").strip(),),
        )
        connection.commit()
    return bool(cursor.rowcount)


def active_digest_subscribers() -> list[dict[str, object]]:
    ensure_database()
    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT *
            FROM digest_subscribers
            WHERE active = 1
            ORDER BY created_at ASC
            """
        ).fetchall()
    subscribers = [dict(row) for row in rows]
    for subscriber in subscribers:
        try:
            subscriber["roles"] = json.loads(str(subscriber.get("roles") or "[]"))
        except json.JSONDecodeError:
            subscriber["roles"] = []
    return subscribers


def send_daily_job_digests() -> dict[str, int]:
    sent = 0
    skipped = 0
    failures = 0
    for subscriber in active_digest_subscribers():
        try:
            result = send_digest_to_subscriber(subscriber)
            if result["sent"]:
                sent += 1
            else:
                skipped += 1
        except Exception:
            failures += 1
    return {"sent": sent, "skipped": skipped, "failures": failures}


def send_digest_to_subscriber(subscriber: dict[str, object]) -> dict[str, object]:
    matches = top_digest_matches(subscriber)
    if not matches:
        return {"sent": False, "matches": []}
    if not (config.SMTP_HOST and config.SMTP_USERNAME):
        return {"sent": False, "matches": matches}

    email = str(subscriber["email"])
    message = EmailMessage()
    message["Subject"] = "Your top 10 job matches for today"
    message["From"] = config.SMTP_USERNAME
    message["To"] = email
    message.set_content(_plain_digest(subscriber, matches))
    message.add_alternative(_html_digest(subscriber, matches), subtype="html")
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as server:
        server.starttls()
        server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
        server.send_message(message)
    _record_deliveries(int(subscriber["id"]), matches)
    return {"sent": True, "matches": matches}


def top_digest_matches(subscriber: dict[str, object]) -> list[dict[str, object]]:
    roles = [str(role) for role in subscriber.get("roles", []) if str(role).strip()]
    location = str(subscriber.get("location") or "").strip()
    resume = {
        "id": int(subscriber["id"]),
        "name": subscriber.get("name") or subscriber["email"],
        "content": subscriber["resume_content"],
    }
    delivered = _delivered_job_ids(int(subscriber["id"]))
    jobs = [
        job
        for job in fetch_jobs()
        if int(job["id"]) not in delivered
        and job.get("application_status") != "Closed"
        and _role_matches(job, roles)
        and location_matches(str(job.get("location") or ""), location)
    ]
    jobs.sort(
        key=lambda job: (
            _posted_at(job),
            int(job.get("resume_match_score") or job.get("local_match_score") or 0),
        ),
        reverse=True,
    )
    candidates = jobs[:MAX_CANDIDATES]
    local_ranked = [
        _format_digest_match(job, _score_resume(job, resume), "local")
        for job in candidates
    ]
    local_ranked.sort(key=lambda item: int(item["score"]), reverse=True)
    gemini_ranked = _score_digest_with_gemini(resume, local_ranked[:DIGEST_LIMIT])
    return (gemini_ranked or local_ranked)[:DIGEST_LIMIT]


def _score_digest_with_gemini(
    resume: dict[str, object],
    matches: list[dict[str, object]],
) -> list[dict[str, object]]:
    api_key = get_user_setting("GEMINI_API_KEY")
    if not api_key or not matches:
        return []
    model = get_user_setting("GEMINI_MODEL") or DEFAULT_MODEL
    jobs_block = "\n\n".join(
        "\n".join(
            [
                f"JOB ID: {match['id']}",
                f"TITLE: {match['title']}",
                f"COMPANY: {match['company']}",
                f"LOCATION: {match['location']}",
                f"ROLE FAMILY: {match['role_query']}",
                f"DESCRIPTION: {str(match.get('description') or '')[:MAX_DIGEST_DESCRIPTION_LENGTH]}",
            ]
        )
        for match in matches
    )
    prompt = f"""
Score these job postings against one subscriber resume for a daily job-match email.

RESUME:
{str(resume['content'])[:MAX_DIGEST_RESUME_LENGTH]}

JOBS:
{jobs_block}

Return one item for every job ID. Score 0-100 using direct evidence from the resume and job
description. Keep rationale under 28 words. Mark hard_no only for explicit legal, citizenship,
clearance, or work-authorization blockers.
""".strip()
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": DIGEST_SCORE_SCHEMA,
            "maxOutputTokens": 2200,
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
    ranked_by_id = {int(match["id"]): match for match in matches}
    scored = []
    for item in json.loads(text).get("matches", []):
        job_id = int(item.get("job_id", 0))
        if job_id not in ranked_by_id:
            continue
        match = ranked_by_id[job_id] | {
            "score": max(0, min(100, int(item.get("score") or 0))),
            "score_source": "Gemini",
            "rationale": str(item.get("rationale") or "").strip(),
            "matched_skills": item.get("matched_skills") if isinstance(item.get("matched_skills"), list) else [],
            "missing_skills": item.get("missing_skills") if isinstance(item.get("missing_skills"), list) else [],
            "hard_no": bool(item.get("hard_no")),
        }
        if match["hard_no"]:
            match["score"] = 0
        scored.append(match)
    scored.sort(key=lambda item: int(item["score"]), reverse=True)
    return scored


def _format_digest_match(
    job: dict[str, object],
    score: dict[str, object],
    score_source: str,
) -> dict[str, object]:
    return {
        "id": int(job["id"]),
        "title": job["title"],
        "company": job["company"],
        "location": job["location"],
        "role_query": job["role_query"],
        "link": job["link"],
        "description": job.get("description", ""),
        "score": int(score.get("score") or 0),
        "score_source": score_source,
        "rationale": "Strongest available match based on resume and job keywords.",
        "matched_skills": score.get("evidence") or [],
        "missing_skills": score.get("missing") or [],
        "hard_no": bool(score.get("hard_no")),
        "posting_date": job.get("posting_date", ""),
    }


def _role_matches(job: dict[str, object], roles: list[str]) -> bool:
    if not roles:
        return True
    job_role = str(job.get("role_query") or "")
    job_title = str(job.get("title") or "")
    job_text = f"{job_title} {job.get('description') or ''}"
    inferred = infer_role_family(job_title, str(job.get("description") or ""))
    normalized_roles = {_normalize(role) for role in roles}
    return (
        _normalize(job_role) in normalized_roles
        or _normalize(inferred) in normalized_roles
        or any(_normalize(role) in _normalize(job_text) for role in roles)
    )


def _delivered_job_ids(subscriber_id: int) -> set[int]:
    ensure_database()
    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute(
            "SELECT job_id FROM digest_deliveries WHERE subscriber_id = ?",
            (subscriber_id,),
        ).fetchall()
    return {int(row[0]) for row in rows}


def _record_deliveries(subscriber_id: int, matches: list[dict[str, object]]) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    ensure_database()
    with sqlite3.connect(DB_PATH) as connection:
        for match in matches:
            connection.execute(
                """
                INSERT OR IGNORE INTO digest_deliveries (
                    subscriber_id, job_id, sent_on, score, score_source
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    subscriber_id,
                    int(match["id"]),
                    today,
                    int(match["score"]),
                    str(match["score_source"]),
                ),
            )
        connection.execute(
            "UPDATE digest_subscribers SET last_sent_at = CURRENT_TIMESTAMP WHERE id = ?",
            (subscriber_id,),
        )
        connection.commit()


def _plain_digest(subscriber: dict[str, object], matches: list[dict[str, object]]) -> str:
    name = str(subscriber.get("name") or "there").strip()
    lines = [f"Hi {name},", "", "Here are your top job matches for today:", ""]
    for index, match in enumerate(matches, start=1):
        lines.extend(
            [
                f"{index}. {match['title']} at {match['company']} - {match['score']}/100 ({match['score_source']})",
                f"   Location: {match['location']}",
                f"   Why: {match['rationale']}",
                f"   Apply: {match['link']}",
                "",
            ]
        )
    lines.append("You are receiving this because you subscribed to JobTracker daily matches.")
    return "\n".join(lines)


def _html_digest(subscriber: dict[str, object], matches: list[dict[str, object]]) -> str:
    name = str(subscriber.get("name") or "there").strip()
    items = []
    for match in matches:
        items.append(
            f"""
            <tr>
              <td style="padding:14px;border-bottom:1px solid #eadfd2;">
                <strong>{_escape(match['title'])}</strong><br>
                {_escape(match['company'])} · {_escape(match['location'])}<br>
                <span style="color:#8a3b2f;">{match['score']}/100 {_escape(match['score_source'])} match</span><br>
                <span>{_escape(match['rationale'])}</span><br>
                <a href="{_escape(match['link'])}">View job</a>
              </td>
            </tr>
            """
        )
    return f"""
    <div style="font-family:Georgia,serif;color:#202b36;background:#fffaf2;padding:20px;">
      <h1 style="margin:0 0 12px;">Your top job matches</h1>
      <p>Hi {_escape(name)}, here are the best new matches for your resume today.</p>
      <table width="100%" cellspacing="0" cellpadding="0">{''.join(items)}</table>
    </div>
    """


def _posted_at(job: dict[str, object]) -> datetime:
    value = str(job.get("posting_date") or "")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc) - timedelta(days=365)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_email(value: str) -> str:
    email = str(value or "").strip().lower()
    return email if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) else ""


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value).lower())).strip()


def _escape(value: object) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
