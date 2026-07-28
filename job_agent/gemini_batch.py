from __future__ import annotations

from datetime import datetime, timezone
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


def submit_gemini_resume_batch() -> dict[str, object]:
    state = batch_status(refresh=False)
    if state.get("active"):
        raise ValueError("A Gemini batch is already active. Refresh it before submitting another.")
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
    from google import genai

    client = genai.Client(api_key=api_key)
    uploaded = client.files.upload(
        file=str(REQUEST_PATH), config={"display_name": "jobtracker-resume-matches", "mime_type": "jsonl"}
    )
    model = get_user_setting("GEMINI_MODEL") or "gemini-3.1-flash-lite"
    batch = client.batches.create(model=model, src=uploaded.name, config={"display_name": "jobtracker-resume-matches"})
    state = {
        "active": True,
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
    if not state or not refresh or not state.get("active"):
        return state or {"active": False, "message": "No Gemini batch submitted."}
    api_key = get_user_setting("GEMINI_API_KEY")
    if not api_key:
        return state | {"message": "Batch exists, but GEMINI_API_KEY is not configured in this process."}
    from google import genai

    client = genai.Client(api_key=api_key)
    try:
        batch = client.batches.get(name=str(state["name"]))
    except Exception as exc:  # noqa: BLE001
        return state | {"message": f"Could not refresh Gemini Batch status: {str(exc)[:160]}"}
    state["provider_state"] = str(getattr(batch, "state", "UNKNOWN"))
    if "SUCCEEDED" in state["provider_state"]:
        completed, failed = _import_results(client, batch)
        state.update({"active": False, "completed": completed, "failed": failed, "message": "Batch results imported."})
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
    for line in content.splitlines():
        item = json.loads(line)
        try:
            job_id = int(item["key"])
            response = item["response"]
            parts = response.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            text = next(part.get("text") for part in parts if part.get("text"))
            rankings = json.loads(text).get("rankings", [])
            _validate_rankings(rankings, {int(resume["id"]) for resume in fetch_resumes()})
            _save_rankings(job_id, rankings)
            completed += 1
        except Exception:
            failed += 1
    return completed, failed


def _has_description(job_id: int) -> bool:
    job = fetch_job(job_id)
    return bool(job and str(job.get("description") or "").strip())


def _read_state() -> dict[str, object]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_state(state: dict[str, object]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
