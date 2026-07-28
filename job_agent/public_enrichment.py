from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
from threading import Event, Lock, Thread
import time

import requests

from job_agent.gemini_analysis import fetch_public_job_details
from job_agent.storage import (
    fetch_job,
    job_ids_for_public_backfill,
    public_description_missing_count,
    save_linkedin_public_capture,
)


logger = logging.getLogger(__name__)
PUBLIC_FETCH_DELAY_SECONDS = 3
PUBLIC_BACKFILL_DELAY_SECONDS = 8
PUBLIC_BACKFILL_LIMIT = 15
MAX_CONSECUTIVE_UNAVAILABLE = 3
OVERNIGHT_BATCH_SIZE = 60
OVERNIGHT_BATCH_INTERVAL_SECONDS = 10 * 60
OVERNIGHT_MAX_BATCHES = 72
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="job-description-capture")
_pending_job_ids: set[int] = set()
_pending_lock = Lock()
_overnight_lock = Lock()
_overnight_stop = Event()
_overnight_halted = Event()
_overnight_wake = Event()
_overnight_status = {
    "running": False,
    "batches": 0,
    "queued": 0,
    "target": 0,
    "processed": 0,
    "captured": 0,
    "unavailable": 0,
    "current_job": "",
    "message": "Not scheduled.",
}


def enqueue_public_description_capture(job_ids: list[int]) -> int:
    return _enqueue_capture(job_ids, PUBLIC_FETCH_DELAY_SECONDS, None, False)


def enqueue_public_description_backfill() -> int:
    return _enqueue_capture(
        job_ids_for_public_backfill(PUBLIC_BACKFILL_LIMIT),
        PUBLIC_BACKFILL_DELAY_SECONDS,
        MAX_CONSECUTIVE_UNAVAILABLE,
        False,
    )


def start_overnight_public_backfill() -> dict[str, object]:
    with _overnight_lock:
        if _overnight_status["running"]:
            _overnight_status["target"] = (
                int(_overnight_status["processed"]) + public_description_missing_count()
            )
            _overnight_status["message"] = "New jobs received; starting the next capture batch."
            _overnight_wake.set()
            return dict(_overnight_status)
        _overnight_stop.clear()
        _overnight_halted.clear()
        _overnight_wake.clear()
        target = public_description_missing_count()
        _overnight_status.update(
            {
                "running": True,
                "batches": 0,
                "queued": 0,
                "target": target,
                "processed": 0,
                "captured": 0,
                "unavailable": 0,
                "current_job": "",
                "message": "Overnight backfill started.",
            }
        )
        Thread(target=_run_overnight_backfill, daemon=True, name="overnight-public-backfill").start()
        return dict(_overnight_status)


def stop_overnight_public_backfill() -> dict[str, object]:
    _overnight_stop.set()
    with _overnight_lock:
        _overnight_status["message"] = "Overnight backfill will stop after the current batch."
        return dict(_overnight_status)


def overnight_public_backfill_status() -> dict[str, object]:
    with _overnight_lock:
        status = dict(_overnight_status)
    status["pending"] = max(0, int(status["queued"]) - int(status["processed"]))
    status["completed"] = int(status["processed"])
    status["remaining"] = max(0, int(status["target"]) - int(status["completed"]))
    return status


def _run_overnight_backfill() -> None:
    try:
        for batch_number in range(1, OVERNIGHT_MAX_BATCHES + 1):
            if _overnight_stop.is_set() or _overnight_halted.is_set():
                break
            queued = _enqueue_capture(
                job_ids_for_public_backfill(OVERNIGHT_BATCH_SIZE),
                PUBLIC_BACKFILL_DELAY_SECONDS,
                MAX_CONSECUTIVE_UNAVAILABLE,
                True,
            )
            with _overnight_lock:
                _overnight_status["batches"] = batch_number
                _overnight_status["queued"] = int(_overnight_status["queued"]) + queued
                _overnight_status["message"] = (
                    f"Batch {batch_number}/{OVERNIGHT_MAX_BATCHES} queued {queued} job(s)."
                )
            if not queued:
                break
            _overnight_wake.wait(OVERNIGHT_BATCH_INTERVAL_SECONDS)
            _overnight_wake.clear()
            if _overnight_stop.is_set():
                break
    finally:
        with _overnight_lock:
            _overnight_status["running"] = False
            if _overnight_halted.is_set():
                _overnight_status["message"] = "Stopped after repeated unavailable public pages."
            elif _overnight_stop.is_set():
                _overnight_status["message"] = "Stopped by you."
            elif int(_overnight_status["batches"]) >= OVERNIGHT_MAX_BATCHES:
                _overnight_status["message"] = "Completed the eight-hour schedule."
            elif not _overnight_status["message"].startswith("Batch"):
                _overnight_status["message"] = "No eligible jobs remained."


def _enqueue_capture(
    job_ids: list[int],
    delay_seconds: int,
    stop_after_unavailable: int | None,
    track_overnight: bool,
) -> int:
    with _pending_lock:
        queued_ids = [int(job_id) for job_id in job_ids if int(job_id) not in _pending_job_ids]
        _pending_job_ids.update(queued_ids)
    if queued_ids:
        _executor.submit(
            _capture_descriptions,
            queued_ids,
            delay_seconds,
            stop_after_unavailable,
            track_overnight,
        )
    return len(queued_ids)


def _capture_descriptions(
    job_ids: list[int],
    delay_seconds: int,
    stop_after_unavailable: int | None,
    track_overnight: bool,
) -> None:
    consecutive_unavailable = 0
    try:
        for job_id in job_ids:
            description = ""
            try:
                job = fetch_job(job_id)
                if not job or str(job.get("description") or "").strip():
                    continue
                if track_overnight:
                    with _overnight_lock:
                        _overnight_status["current_job"] = str(job.get("title") or "LinkedIn job")
                        _overnight_status["message"] = "Capturing public posting details..."
                try:
                    captured = fetch_public_job_details(str(job.get("link") or ""))
                except requests.RequestException as exc:
                    logger.info("Public description unavailable for job %s: %s", job_id, exc)
                    captured = {"description": "", "metadata": {}}
                description = str(captured.get("description") or "")
                metadata = captured.get("metadata") if isinstance(captured.get("metadata"), dict) else {}
                updated_ids = save_linkedin_public_capture(str(job["link"]), description, metadata)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not enrich job %s from its public page: %s", job_id, exc)

            if track_overnight:
                with _overnight_lock:
                    _overnight_status["processed"] = int(_overnight_status["processed"]) + 1
                    if description:
                        _overnight_status["captured"] = int(_overnight_status["captured"]) + 1
                    else:
                        _overnight_status["unavailable"] = int(_overnight_status["unavailable"]) + 1
                    _overnight_status["message"] = "Waiting before the next public page..."
            consecutive_unavailable = 0 if description else consecutive_unavailable + 1
            if stop_after_unavailable and consecutive_unavailable >= stop_after_unavailable:
                logger.info("Stopped public description backfill after repeated unavailable pages.")
                _overnight_halted.set()
                break
            time.sleep(delay_seconds)
    finally:
        with _pending_lock:
            _pending_job_ids.difference_update(job_ids)
