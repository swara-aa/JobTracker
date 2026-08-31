from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
from threading import Event, Lock, Thread
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from job_agent.config import (
    AUTOMATION_PUBLIC_COLLECTION_TIME,
    DATA_DIR,
    DIGEST_SEND_TIME,
    DIGEST_TIMEZONE,
    get_user_setting,
)


logger = logging.getLogger(__name__)
STATE_PATH = DATA_DIR / "automation_state.json"
POLL_SECONDS = 60
BATCH_REFRESH_SECONDS = 5 * 60
LINKEDIN_CAPTURE_COOLDOWN_SECONDS = 2 * 60
PUBLIC_CAPTURE_DELAY_SECONDS = 10 * 60
PUBLIC_GEMINI_DELAY_SECONDS = 15 * 60
RETRY_GEMINI_DELAY_SECONDS = 2 * 60
MAX_DAILY_BATCH_SUBMISSIONS = 3
MAX_DAILY_BATCH_FAILURES = 3
GEMINI_SUBMISSION_STATE_VERSION = "batch-file-readiness-v2"
_state_lock = Lock()
_thread_lock = Lock()
_wake = Event()
_thread: Thread | None = None
_runtime = {
    "running": False,
    "phase": "stopped",
    "last_error": "",
}


def start_automation_coordinator() -> dict[str, object]:
    global _thread
    with _thread_lock:
        if _thread and _thread.is_alive():
            return automation_status()
        _thread = Thread(
            target=_run_loop,
            daemon=True,
            name="jobtracker-automation",
        )
        _thread.start()
    return automation_status()


def run_automation_forever() -> None:
    _runtime.update({"running": True, "phase": "starting", "last_error": ""})
    while True:
        try:
            _tick()
            state = _read_state()
            state.update(
                {
                    "worker_mode": "azure-webjob",
                    "worker_heartbeat_at": _now().isoformat(),
                    "worker_last_error": "",
                }
            )
            _write_state(state)
            _runtime["last_error"] = ""
        except Exception as exc:  # noqa: BLE001
            logger.exception("Automation worker failed: %s", exc)
            error = str(exc).replace("\n", " ")[:300]
            _runtime["last_error"] = error
            _runtime["phase"] = "waiting after error"
            state = _read_state()
            state.update(
                {
                    "worker_mode": "azure-webjob",
                    "worker_heartbeat_at": _now().isoformat(),
                    "worker_last_error": error,
                }
            )
            _write_state(state)
        _wake.wait(POLL_SECONDS)
        _wake.clear()


def schedule_linkedin_postprocessing(job_ids: list[int]) -> dict[str, object]:
    now = _now()
    state = _read_state()
    state.update(
        {
            "linkedin_last_finished_at": now.isoformat(),
            "linkedin_jobs_collected": len(set(int(job_id) for job_id in job_ids)),
            "description_capture_pending": True,
            "description_capture_not_before": (
                now + timedelta(seconds=LINKEDIN_CAPTURE_COOLDOWN_SECONDS)
            ).isoformat(),
            "gemini_not_before": (
                now + timedelta(seconds=LINKEDIN_CAPTURE_COOLDOWN_SECONDS)
            ).isoformat(),
            "message": (
                "LinkedIn collection finished. Waiting two minutes before "
                "public-description capture."
            ),
        }
    )
    _write_state(state)
    _wake.set()
    return automation_status()


def schedule_public_postprocessing(job_ids: list[int]) -> dict[str, object]:
    if not job_ids:
        return automation_status()
    now = _now()
    state = _read_state()
    state.update(
        {
            "gemini_not_before": (
                now + timedelta(seconds=LINKEDIN_CAPTURE_COOLDOWN_SECONDS)
            ).isoformat(),
            "message": (
                f"Saved {len(job_ids)} public-board job(s). Gemini will run "
                "after the quiet period."
            ),
        }
    )
    _write_state(state)
    _wake.set()
    return automation_status()


def automation_status() -> dict[str, object]:
    state = _read_state()
    with _thread_lock:
        thread_running = bool(_thread and _thread.is_alive())
    worker_recent = _worker_heartbeat_recent(str(state.get("worker_heartbeat_at") or ""))
    if worker_recent:
        runtime = dict(_runtime)
        runtime["running"] = True
        runtime["phase"] = str(state.get("worker_mode") or "azure-webjob")
        runtime["last_error"] = str(state.get("worker_last_error") or "")
        return state | runtime
    return state | dict(_runtime) | {"running": thread_running}


def _worker_heartbeat_recent(value: str) -> bool:
    heartbeat = _parse_time(value)
    return bool(heartbeat and _now() - heartbeat < timedelta(minutes=5))


def _run_loop() -> None:
    _runtime.update({"running": True, "phase": "starting", "last_error": ""})
    while True:
        try:
            _tick()
            _runtime["last_error"] = ""
        except Exception as exc:  # noqa: BLE001
            logger.exception("Automation coordinator failed: %s", exc)
            _runtime["last_error"] = str(exc).replace("\n", " ")[:300]
            _runtime["phase"] = "waiting after error"
        _wake.wait(POLL_SECONDS)
        _wake.clear()


def _tick() -> None:
    _reset_daily_batch_counter()
    _bootstrap_pending_work()
    _maybe_collect_public_boards()
    _maybe_start_description_capture()
    _maybe_score_described_jobs_locally()
    _maybe_refresh_gemini_batch()
    _maybe_submit_gemini_batch()
    _maybe_send_daily_digests()
    if _runtime["phase"] not in {
        "collecting public boards",
        "starting description capture",
        "preparing Gemini batch",
        "refreshing Gemini batch",
        "sending daily digests",
    }:
        _runtime["phase"] = "monitoring"


def _bootstrap_pending_work() -> None:
    from job_agent.gemini_batch import batch_status
    from job_agent.public_enrichment import overnight_public_backfill_status
    from job_agent.storage import (
        described_job_ids_without_gemini_match,
        fetch_resumes,
        public_description_missing_count,
    )

    state = _read_state()
    changed = False
    now = _now()
    capture = overnight_public_backfill_status()
    if (
        public_description_missing_count() > 0
        and not capture.get("running")
        and not state.get("description_capture_pending")
    ):
        state["description_capture_pending"] = True
        state["description_capture_not_before"] = (
            now + timedelta(seconds=LINKEDIN_CAPTURE_COOLDOWN_SECONDS)
        ).isoformat()
        changed = True
    can_run_gemini = bool(get_user_setting("GEMINI_API_KEY") and fetch_resumes())
    gemini_batch = batch_status(refresh=False)
    if (
        described_job_ids_without_gemini_match()
        and not gemini_batch.get("active")
        and not gemini_batch.get("submission_in_progress")
        and not state.get("gemini_not_before")
        and can_run_gemini
        and int(state.get("batch_submissions") or 0) < MAX_DAILY_BATCH_SUBMISSIONS
        and int(state.get("gemini_submission_failures") or 0) < MAX_DAILY_BATCH_FAILURES
    ):
        state["gemini_not_before"] = (
            now + timedelta(seconds=LINKEDIN_CAPTURE_COOLDOWN_SECONDS)
        ).isoformat()
        changed = True
    if changed:
        state["message"] = "Resuming unfinished automatic post-processing."
        _write_state(state)


def _reset_daily_batch_counter() -> None:
    stored_state = _read_stored_state()
    state = _default_state() | stored_state
    if stored_state.get("gemini_submission_state_version") != GEMINI_SUBMISSION_STATE_VERSION:
        state.update(
            {
                "gemini_submission_state_version": GEMINI_SUBMISSION_STATE_VERSION,
                "batch_submissions": 0,
                "gemini_submission_failures": 0,
                "gemini_not_before": (_now() + timedelta(seconds=LINKEDIN_CAPTURE_COOLDOWN_SECONDS)).isoformat(),
                "message": "Gemini batch recovery has been updated; preparing one safe retry.",
            }
        )
        _write_state(state)
        return
    today = _now().date().isoformat()
    if state.get("batch_date") == today:
        return
    state.update(
        {
            "batch_date": today,
            "batch_submissions": 0,
            "gemini_submission_failures": 0,
        }
    )
    _write_state(state)


def _maybe_collect_public_boards() -> None:
    now = _now()
    today = now.date().isoformat()
    state = _read_state()
    if state.get("last_public_collection_date") == today:
        return
    hour, minute = _automation_time()
    if (now.hour, now.minute) < (hour, minute):
        return
    state.update(
        {
            "last_public_collection_date": today,
            "message": "Collecting configured public job boards...",
        }
    )
    _write_state(state)
    _runtime["phase"] = "collecting public boards"
    from job_agent.collector import run_collection_and_prepare_matches

    result = run_collection_and_prepare_matches(submit_gemini=False)
    saved_ids = [int(job_id) for job_id in result["saved_job_ids"]]
    now = _now()
    state = _read_state()
    state.update(
        {
            "public_jobs_saved": len(saved_ids),
            "public_collection_finished_at": now.isoformat(),
            "message": f"Public-board collection saved {len(saved_ids)} new job(s).",
        }
    )
    if saved_ids:
        state["description_capture_pending"] = True
        state["description_capture_not_before"] = (
            now + timedelta(seconds=PUBLIC_CAPTURE_DELAY_SECONDS)
        ).isoformat()
        state["gemini_not_before"] = (
            now + timedelta(seconds=PUBLIC_GEMINI_DELAY_SECONDS)
        ).isoformat()
    _write_state(state)


def _maybe_start_description_capture() -> None:
    state = _read_state()
    if not state.get("description_capture_pending"):
        return
    if not _time_reached(str(state.get("description_capture_not_before") or "")):
        return
    _runtime["phase"] = "starting description capture"
    from job_agent.public_enrichment import start_overnight_public_backfill

    capture = start_overnight_public_backfill()
    state.update(
        {
            "description_capture_pending": False,
            "message": str(capture["message"]),
        }
    )
    _write_state(state)


def _maybe_score_described_jobs_locally() -> None:
    """Keep the no-cost score current even when Gemini is paused or unavailable."""
    from job_agent.storage import described_job_ids_without_local_score, fetch_resumes

    if not fetch_resumes():
        return
    job_ids = described_job_ids_without_local_score()
    if not job_ids:
        return
    _runtime["phase"] = "updating local match scores"
    from job_agent.local_scoring import score_jobs_locally

    result = score_jobs_locally(job_ids)
    state = _read_state()
    state["message"] = f"Updated local match scores for {int(result['scored'])} described job(s)."
    _write_state(state)


def _maybe_refresh_gemini_batch() -> None:
    from job_agent.gemini_batch import batch_status

    batch = batch_status(refresh=False)
    if not batch.get("active"):
        return
    state = _read_state()
    last_refresh = _parse_time(str(state.get("last_batch_refresh_at") or ""))
    if last_refresh and _now() - last_refresh < timedelta(seconds=BATCH_REFRESH_SECONDS):
        return
    _runtime["phase"] = "refreshing Gemini batch"
    refreshed = batch_status(refresh=True)
    now = _now()
    state.update(
        {
            "last_batch_refresh_at": now.isoformat(),
            "message": str(refreshed.get("message") or "Gemini batch refreshed."),
        }
    )
    terminal_failure = any(
        word in str(refreshed.get("provider_state") or "")
        for word in ("FAILED", "CANCELLED", "EXPIRED")
    )
    if not refreshed.get("active") and (
        int(refreshed.get("failed") or 0) or terminal_failure
    ):
        state["gemini_not_before"] = (
            now + timedelta(seconds=RETRY_GEMINI_DELAY_SECONDS)
        ).isoformat()
    _write_state(state)


def _maybe_submit_gemini_batch() -> None:
    state = _read_state()
    if not _time_reached(str(state.get("gemini_not_before") or "")):
        return
    from job_agent.gemini_batch import batch_status, submit_gemini_resume_batch
    from job_agent.storage import (
        described_job_ids_without_gemini_match,
        fetch_resumes,
    )

    current_batch = batch_status(refresh=False)
    if current_batch.get("active") or current_batch.get("submission_in_progress"):
        return
    if not get_user_setting("GEMINI_API_KEY") or not fetch_resumes():
        state.update(
            {
                "gemini_not_before": "",
                "message": "Gemini automation is waiting for an API key and uploaded resume.",
            }
        )
        _write_state(state)
        return
    job_ids = described_job_ids_without_gemini_match()
    if not job_ids:
        state.update(
            {
                "gemini_not_before": "",
                "message": "Post-processing complete; no described jobs need Gemini scoring.",
            }
        )
        _write_state(state)
        return
    submissions = int(state.get("batch_submissions") or 0)
    failures = int(state.get("gemini_submission_failures") or 0)
    if submissions >= MAX_DAILY_BATCH_SUBMISSIONS:
        state.update(
            {
                "gemini_not_before": "",
                "message": (
                    "Gemini retry limit reached for today. Remaining failures "
                    "will stay visible in Operations."
                ),
            }
        )
        _write_state(state)
        return
    if failures >= MAX_DAILY_BATCH_FAILURES:
        state.update(
            {
                "gemini_not_before": "",
                "message": (
                    "Gemini submission failed three times today. Automatic retry resumes tomorrow; "
                    "check Gemini billing and API region in Operations before forcing another submission."
                ),
            }
        )
        _write_state(state)
        return
    _runtime["phase"] = "preparing Gemini batch"
    from job_agent.local_scoring import score_jobs_locally
    from job_agent.visa_analysis import reassess_explicit_posting_language

    score_jobs_locally(job_ids)
    reassess_explicit_posting_language(job_ids)
    state.update(
        {
            "gemini_not_before": "",
            "message": f"Submitting {len(job_ids)} described job(s) to Gemini Batch...",
        }
    )
    _write_state(state)
    try:
        batch = submit_gemini_resume_batch()
    except Exception as exc:
        state = _read_state()
        state.update(
            {
                "gemini_submission_failures": failures + 1,
                "gemini_not_before": (
                    _now() + timedelta(minutes=15)
                ).isoformat(),
                "message": _gemini_submission_failure_message(exc, failures + 1),
            }
        )
        _write_state(state)
        return
    state = _read_state()
    state.update(
        {
            "batch_submissions": submissions + 1,
            "gemini_submission_failures": 0,
            "message": str(batch["message"]),
        }
    )
    _write_state(state)


def _maybe_send_daily_digests() -> None:
    now = _digest_now()
    today = now.date().isoformat()
    state = _read_state()
    if state.get("last_digest_date") == today:
        return
    hour, minute = _digest_time()
    if (now.hour, now.minute) < (hour, minute):
        return
    _runtime["phase"] = "sending daily digests"
    from job_agent.digest import send_daily_job_digests

    result = send_daily_job_digests()
    state.update(
        {
            "last_digest_date": today,
            "last_digest_finished_at": _now().isoformat(),
            "message": (
                f"Daily digest sent to {result['sent']} subscriber(s); "
                f"{result['skipped']} skipped; {result['failures']} failed."
            ),
        }
    )
    _write_state(state)


def _gemini_submission_failure_message(error: Exception, failures: int) -> str:
    detail = str(error).replace("\n", " ")[:180]
    guidance = ""
    if "FAILED_PRECONDITION" in detail.upper():
        guidance = " Check Gemini billing and that the API is available from this server region."
    return f"Gemini submission attempt {failures}/{MAX_DAILY_BATCH_FAILURES} failed: {detail}.{guidance} Retrying in 15 minutes."


def _automation_time() -> tuple[int, int]:
    try:
        hour, minute = AUTOMATION_PUBLIC_COLLECTION_TIME.split(":", 1)
        parsed = int(hour), int(minute)
        if 0 <= parsed[0] <= 23 and 0 <= parsed[1] <= 59:
            return parsed
    except (TypeError, ValueError):
        pass
    return 7, 56


def _digest_time() -> tuple[int, int]:
    try:
        hour, minute = DIGEST_SEND_TIME.split(":", 1)
        parsed = int(hour), int(minute)
        if 0 <= parsed[0] <= 23 and 0 <= parsed[1] <= 59:
            return parsed
    except (TypeError, ValueError):
        pass
    return 8, 0


def _digest_now() -> datetime:
    try:
        return datetime.now(ZoneInfo(DIGEST_TIMEZONE))
    except ZoneInfoNotFoundError:
        return _now()


def _time_reached(value: str) -> bool:
    parsed = _parse_time(value)
    return bool(parsed and _now() >= parsed)


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed


def _now() -> datetime:
    return datetime.now().astimezone()


def _default_state() -> dict[str, object]:
    return {
        "last_public_collection_date": "",
        "public_collection_finished_at": "",
        "public_jobs_saved": 0,
        "linkedin_last_finished_at": "",
        "linkedin_jobs_collected": 0,
        "description_capture_pending": False,
        "description_capture_not_before": "",
        "gemini_not_before": "",
        "last_batch_refresh_at": "",
        "batch_date": "",
        "batch_submissions": 0,
        "gemini_submission_failures": 0,
        "gemini_submission_state_version": GEMINI_SUBMISSION_STATE_VERSION,
        "last_digest_date": "",
        "last_digest_finished_at": "",
        "worker_mode": "",
        "worker_heartbeat_at": "",
        "worker_last_error": "",
        "message": "Automation coordinator is starting.",
    }


def _read_state() -> dict[str, object]:
    return _default_state() | _read_stored_state()


def _read_stored_state() -> dict[str, object]:
    with _state_lock:
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}


def _write_state(state: dict[str, object]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = _default_state() | state
    temporary_path = Path(f"{STATE_PATH}.tmp")
    with _state_lock:
        temporary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary_path.replace(STATE_PATH)
