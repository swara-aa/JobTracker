from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
from threading import Lock
import time

from job_agent.config import get_user_setting
from job_agent.resume_matcher import compare_resumes
from job_agent.storage import fetch_resumes, job_ids_without_gemini_match


logger = logging.getLogger(__name__)
GEMINI_MATCH_DELAY_SECONDS = 8
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gemini-resume-match")
_pending_ids: set[int] = set()
_lock = Lock()
_status = {"running": False, "queued": 0, "completed": 0, "failed": 0, "current_job_id": None}


def enqueue_gemini_resume_comparisons(job_ids: list[int]) -> int:
    if not get_user_setting("GEMINI_API_KEY") or not fetch_resumes():
        return 0
    with _lock:
        new_ids = [job_id for job_id in job_ids if job_id not in _pending_ids]
        _pending_ids.update(new_ids)
        if new_ids:
            _status.update({"running": True, "queued": int(_status["queued"]) + len(new_ids)})
            _executor.submit(_run, new_ids)
    return len(new_ids)


def start_gemini_resume_backlog() -> int:
    return enqueue_gemini_resume_comparisons(job_ids_without_gemini_match())


def gemini_queue_status() -> dict[str, object]:
    with _lock:
        status = dict(_status)
    status["pending"] = max(0, int(status["queued"]) - int(status["completed"]) - int(status["failed"]))
    return status


def _run(job_ids: list[int]) -> None:
    try:
        for job_id in job_ids:
            with _lock:
                _status["current_job_id"] = job_id
            try:
                compare_resumes(job_id)
                with _lock:
                    _status["completed"] = int(_status["completed"]) + 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Gemini resume match failed for job %s: %s", job_id, exc)
                with _lock:
                    _status["failed"] = int(_status["failed"]) + 1
            time.sleep(GEMINI_MATCH_DELAY_SECONDS)
    finally:
        with _lock:
            _pending_ids.difference_update(job_ids)
            _status["running"] = bool(_pending_ids)
            if not _status["running"]:
                _status["current_job_id"] = None
