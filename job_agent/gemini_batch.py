from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from job_agent.config import DATA_DIR, get_user_setting
from job_agent.resume_matcher import (
    MATCH_SCHEMA,
    MAX_MATCH_DESCRIPTION_LENGTH,
    MAX_MATCH_RESUME_LENGTH,
    _save_rankings,
    _validate_rankings,
)
from job_agent.storage import fetch_job, fetch_resumes, job_ids_without_gemini_match


STATE_PATH = DATA_DIR / "gemini_batch_state.json"
REQUEST_PATH = DATA_DIR / "gemini_resume_batch.jsonl"
FAILURE_PATH = DATA_DIR / "gemini_batch_failures.json"
GEMINI_HTTP_TIMEOUT_MS = 120_000


def submit_gemini_resume_batch() -> dict[str, object]:
    state = batch_status(refresh=False)
    if state.get("active") or state.get("submission_in_progress"):
        raise ValueError("A Gemini batch submission is already active.")
    api_key = get_user_setting("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not configured.")
    resumes = fetch_resumes()
    if not resumes:
        raise ValueError("Upload at least one resume before submitting a Gemini batch.")
    job_ids = [job_id for job_id in job_ids_without_gemini_match() if _has_description(job_id)]
    if not job_ids:
        return {"active": False, "message": "No described jobs are waiting for Gemini scoring."}
    _write_requests(job_ids, resumes)
    model = get_user_setting("GEMINI_MODEL") or "gemini-3.1-flash-lite"
    preparing_state = {
        "active": False,
        "submission_in_progress": True,
        "model": model,
        "submitted_at": "",
        "submission_started_at": datetime.now(timezone.utc).isoformat(),
        "total": len(job_ids),
        "completed": 0,
        "failed": 0,
        "message": f"Uploading {len(job_ids)} jobs to Gemini Batch.",
    }
    _write_state(preparing_state)
    try:
        with _gemini_client(api_key) as client:
            uploaded = client.files.upload(
                file=str(REQUEST_PATH),
                config={
                    "display_name": "jobtracker-resume-matches",
                    "mime_type": "jsonl",
                },
            )
            batch = client.batches.create(
                model=model,
                src=uploaded.name,
                config={"display_name": "jobtracker-resume-matches"},
            )
    except Exception as exc:
        _write_state(
            preparing_state
            | {
                "submission_in_progress": False,
                "message": f"Gemini Batch submission failed: {str(exc)[:180]}",
            }
        )
        raise
    state = {
        "active": True,
        "submission_in_progress": False,
        "name": str(batch.name),
        "model": model,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "total": len(job_ids),
        "completed": 0,
        "failed": 0,
        "message": "Submitted to Gemini Batch API.",
    }
    _write_state(state)
    return state


def batch_status(refresh: bool = False) -> dict[str, object]:
    state = _read_state()
    if state.get("submission_in_progress") and _submission_is_stale(state):
        state.update(
            {
                "submission_in_progress": False,
                "message": "The previous Gemini upload was interrupted and can be retried.",
            }
        )
        _write_state(state)
    if not state or not refresh or not state.get("active"):
        return state or {"active": False, "message": "No Gemini batch submitted."}
    api_key = get_user_setting("GEMINI_API_KEY")
    if not api_key:
        return state | {"message": "Batch exists, but GEMINI_API_KEY is not configured in this process."}
    try:
        with _gemini_client(api_key) as client:
            batch = client.batches.get(name=str(state["name"]))
            if "SUCCEEDED" in str(getattr(batch, "state", "UNKNOWN")):
                completed, failed = _import_results(client, batch)
    except Exception as exc:  # noqa: BLE001
        return state | {"message": f"Could not refresh Gemini Batch status: {str(exc)[:160]}"}
    state["provider_state"] = str(getattr(batch, "state", "UNKNOWN"))
    if "SUCCEEDED" in state["provider_state"]:
        message = "Batch results imported."
        if failed:
            message += " Review the recorded batch failures before retrying them."
        state.update(
            {
                "active": False,
                "completed": completed,
                "failed": failed,
                "failure_details_path": str(FAILURE_PATH) if failed else "",
                "message": message,
            }
        )
    elif any(word in state["provider_state"] for word in ("FAILED", "CANCELLED", "EXPIRED")):
        state.update({"active": False, "message": f"Batch ended: {state['provider_state']}"})
    else:
        state["message"] = f"Gemini Batch status: {state['provider_state']}"
    _write_state(state)
    return state


def _write_requests(job_ids: list[int], resumes: list[dict[str, object]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with REQUEST_PATH.open("w", encoding="utf-8") as stream:
        for job_id in job_ids:
            stream.write(json.dumps({"key": str(job_id), "request": _request_for(job_id, resumes)}) + "\n")


def _request_for(job_id: int, resumes: list[dict[str, object]]) -> dict[str, object]:
    job = fetch_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} no longer exists.")
    resume_sections = "\n\n".join(
        f"=== RESUME ID {resume['id']}: {resume['name']} ===\n{str(resume['content'])[:MAX_MATCH_RESUME_LENGTH]}"
        for resume in resumes
    )
    prompt = f"""Compare each resume with this job posting and rank its application fit.\n\nJOB: {job['title']} at {job['company']}\nLOCATION: {job['location']}\nJOB DESCRIPTION:\n{str(job['description'])[:MAX_MATCH_DESCRIPTION_LENGTH]}\n\nRESUMES:\n{resume_sections}\n\nReturn one ranking for every resume ID. Score 0-100 using required skills (35), relevant experience/projects (30), education/baseline qualifications (15), preferred skills (10), and clarity/evidence (10). Do not invent qualifications. Return exact posting terms for matched_skills and missing_skills. Set hard_no=true only for explicit citizenship, clearance, or permanent-work-authorization-without-sponsorship requirements."""
    return {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generation_config": {"response_mime_type": "application/json", "response_schema": MATCH_SCHEMA, "max_output_tokens": 2500},
    }


def _import_results(client: Any, batch: Any) -> tuple[int, int]:
    output_name = str(getattr(getattr(batch, "dest", None), "file_name", ""))
    if not output_name:
        raise RuntimeError("Gemini batch completed without an output file.")
    content = client.files.download(file=output_name).decode("utf-8")
    completed = failed = 0
    failures: list[dict[str, object]] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        job_id: int | None = None
        try:
            item = json.loads(line)
            job_id = int(item["key"])
            if item.get("error"):
                raise RuntimeError(str(item["error"]))
            response = item["response"]
            parts = response.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            text = next(part.get("text") for part in parts if part.get("text"))
            rankings = json.loads(text).get("rankings", [])
            _validate_rankings(rankings, {int(resume["id"]) for resume in fetch_resumes()})
            _save_rankings(job_id, rankings)
            completed += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            failures.append(
                {
                    "job_id": job_id,
                    "line": line_number,
                    "error": str(exc)[:1000],
                }
            )
    if failures:
        FAILURE_PATH.write_text(json.dumps(failures, indent=2), encoding="utf-8")
    elif FAILURE_PATH.exists():
        FAILURE_PATH.unlink()
    return completed, failed


def _has_description(job_id: int) -> bool:
    job = fetch_job(job_id)
    return bool(job and str(job.get("description") or "").strip())


def _gemini_client(api_key: str):
    from google import genai
    from google.genai import types

    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=GEMINI_HTTP_TIMEOUT_MS),
    )


def _submission_is_stale(state: dict[str, object]) -> bool:
    try:
        started_at = datetime.fromisoformat(str(state["submission_started_at"]))
    except (KeyError, TypeError, ValueError):
        return True
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - started_at.astimezone(timezone.utc) > timedelta(
        minutes=5
    )


def _read_state() -> dict[str, object]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_state(state: dict[str, object]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
