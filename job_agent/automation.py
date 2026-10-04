from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
from threading import Event, Lock, Thread
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from job_agent.config import (
    AUTOMATION_PUBLIC_COLLECTION_TIME,
    DIGEST_LATEST_SEND_TIME,
    DATA_DIR,
    DIGEST_SEND_TIME,
    DIGEST_TIMEZONE,
    GEMINI_BATCH_SIZE,
    PRIORITY_GEMINI_DAILY_LIMIT,
    get_user_setting,
)
from job_agent.notifications import send_operational_alert


logger = logging.getLogger(__name__)
STATE_PATH = DATA_DIR / "automation_state.json"
POLL_SECONDS = 60
BATCH_REFRESH_SECONDS = 5 * 60
LINKEDIN_CAPTURE_COOLDOWN_SECONDS = 2 * 60
PUBLIC_CAPTURE_DELAY_SECONDS = 10 * 60
PUBLIC_GEMINI_DELAY_SECONDS = 15 * 60
RETRY_GEMINI_DELAY_SECONDS = 2 * 60
PRIORITY_GEMINI_RETRY_SECONDS = 15 * 60
GEMINI_BLOCKING_RETRY_SECONDS = 6 * 60 * 60
PRIORITY_GEMINI_PER_TICK = 10
DIGEST_RETRY_SECONDS = 30 * 60
MAX_DAILY_DIGEST_ATTEMPTS = 3
MAX_DAILY_BATCH_SUBMISSIONS = 3
MAX_DAILY_BATCH_FAILURES = 3
GEMINI_SUBMISSION_STATE_VERSION = "batch-noop-safe-v3"
_state_lock = Lock()
_thread_lock = Lock()
_wake = Event()
_thread: Thread | None = None
_runtime = {
    "running": False,
    "phase": "stopped",
    "last_error": "",
}


def _send_failure_alert_once(
    state: dict[str, object],
    key: str,
    subject: str,
    body: str,
) -> bool:
    marker_key = f"alert_{key}_date"
    signature_key = f"alert_{key}_signature"
    today = _digest_now().date().isoformat()
    signature = body[:240]
    if state.get(marker_key) == today and state.get(signature_key) == signature:
        return False
    try:
        sent = send_operational_alert(subject, body)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Operational alert failed: %s", exc)
        return False
    if sent:
        state[marker_key] = today
        state[signature_key] = signature
    return sent


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
            _send_failure_alert_once(
                state,
                "worker_failure",
                "Automation worker failure",
                f"The automation worker failed and will retry.\n\nError: {error}",
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


def request_public_collection_now() -> dict[str, object]:
    state = _read_state()
    state.update(
        {
            "force_public_collection_pending": True,
            "message": "Public-board collection has been queued for the automation worker.",
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
    _maybe_score_recent_jobs_with_gemini()
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
    today = _digest_now().date().isoformat()
    batch_is_current = state.get("batch_date") == today
    priority_is_current = state.get("priority_gemini_date") == today
    digest_is_current = state.get("digest_attempt_date") == today
    if batch_is_current and priority_is_current and digest_is_current:
        return
    if not batch_is_current:
        state.update(
            {
                "batch_date": today,
                "batch_submissions": 0,
                "gemini_submission_failures": 0,
            }
        )
    if not priority_is_current:
        state.update(
            {
                "priority_gemini_date": today,
                "priority_gemini_requests": 0,
                "priority_gemini_scored": 0,
                "priority_gemini_retry_not_before": "",
                "priority_gemini_last_error": "",
            }
        )
    if not digest_is_current:
        state.update(
            {
                "digest_attempt_date": today,
                "digest_attempts": 0,
                "digest_retry_not_before": "",
            }
        )
    _write_state(state)


def _maybe_collect_public_boards() -> None:
    now = _digest_now()
    today = now.date().isoformat()
    state = _read_state()
    force_run = bool(state.get("force_public_collection_pending"))
    if state.get("last_public_collection_date") == today and not force_run:
        return
    hour, minute = _automation_time()
    if (now.hour, now.minute) < (hour, minute) and not force_run:
        return
    state.update(
        {
            "last_public_collection_date": today,
            "force_public_collection_pending": False,
            "message": "Collecting configured public job boards...",
        }
    )
    _write_state(state)
    _runtime["phase"] = "collecting public boards"
    from job_agent.collector import run_collection_and_prepare_matches

    try:
        result = run_collection_and_prepare_matches(submit_gemini=False)
    except Exception as exc:
        error = str(exc).replace("\n", " ")[:300]
        state = _read_state()
        state.update(
            {
                "public_collection_finished_at": _now().isoformat(),
                "message": f"Public-board collection failed: {error}",
            }
        )
        _send_failure_alert_once(
            state,
            "public_collection_failure",
            "Public-board collection failure",
            f"Public job collection failed.\n\nError: {error}",
        )
        _write_state(state)
        raise
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


def _maybe_score_recent_jobs_with_gemini() -> None:
    """Score new jobs promptly without waiting for a historical backlog batch."""
    from job_agent.storage import (
        fetch_resumes,
        recent_described_job_ids_without_gemini_match,
    )

    state = _read_state()
    retry_at = str(state.get("priority_gemini_retry_not_before") or "")
    if retry_at and not _time_reached(retry_at):
        return
    requests_today = int(state.get("priority_gemini_requests") or 0)
    remaining = PRIORITY_GEMINI_DAILY_LIMIT - requests_today
    if remaining <= 0:
        return
    if not get_user_setting("GEMINI_API_KEY") or not fetch_resumes():
        return
    local_now = _digest_now()
    local_day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    job_ids = recent_described_job_ids_without_gemini_match(
        limit=min(PRIORITY_GEMINI_PER_TICK, remaining),
        collected_since=local_day_start,
    )
    if not job_ids:
        state["priority_gemini_retry_not_before"] = ""
        state["priority_gemini_last_error"] = ""
        _write_state(state)
        return

    _runtime["phase"] = "scoring recent jobs with Gemini"
    from job_agent.resume_matcher import compare_resumes

    completed = 0
    last_error = ""
    for job_id in job_ids:
        requests_today += 1
        try:
            compare_resumes(job_id)
            completed += 1
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc).replace("\n", " ")[:240]
            break

    state = _read_state()
    state["priority_gemini_requests"] = requests_today
    state["priority_gemini_scored"] = (
        int(state.get("priority_gemini_scored") or 0) + completed
    )
    state["priority_gemini_last_error"] = last_error
    if last_error:
        retry_seconds, retry_label = _gemini_retry_policy(last_error)
        state["priority_gemini_retry_not_before"] = (
            _now() + timedelta(seconds=retry_seconds)
        ).isoformat()
        state["message"] = (
            f"Gemini scored {completed} recent job(s), then paused: {last_error}. "
            f"Retrying {retry_label}."
        )
        _send_failure_alert_once(
            state,
            "priority_gemini_failure",
            "Gemini recent-job scoring failure",
            (
                f"Gemini recent-job scoring paused after {completed} job(s).\n\n"
                f"Error: {last_error}\nRetry: {retry_label}"
            ),
        )
    else:
        state["priority_gemini_retry_not_before"] = ""
        state["message"] = f"Gemini scored {completed} recent job(s) immediately."
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
    elif not refreshed.get("active") and int(refreshed.get("completed") or 0):
        state["gemini_submission_failures"] = 0
        state["priority_gemini_last_error"] = ""
        state["priority_gemini_retry_not_before"] = ""
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
    job_ids = described_job_ids_without_gemini_match(limit=GEMINI_BATCH_SIZE)
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
        batch = submit_gemini_resume_batch(job_ids)
    except Exception as exc:
        retry_seconds, retry_label = _gemini_retry_policy(exc)
        state = _read_state()
        state.update(
            {
                "gemini_submission_failures": failures + 1,
                "gemini_not_before": (
                    _now() + timedelta(seconds=retry_seconds)
                ).isoformat(),
                "message": _gemini_submission_failure_message(
                    exc,
                    failures + 1,
                    retry_label=retry_label,
                ),
            }
        )
        _send_failure_alert_once(
            state,
            "gemini_batch_submission_failure",
            "Gemini batch submission failure",
            (
                f"Gemini batch submission failed on attempt {failures + 1}/"
                f"{MAX_DAILY_BATCH_FAILURES}.\n\nError: "
                f"{str(exc).replace(chr(10), ' ')[:300]}"
            ),
        )
        _write_state(state)
        return
    state = _read_state()
    update: dict[str, object] = {
        "gemini_submission_failures": 0,
        "message": str(batch["message"]),
    }
    if batch.get("active") or batch.get("submission_in_progress") or batch.get("name"):
        update["batch_submissions"] = submissions + 1
    state.update(
        update
    )
    _write_state(state)


def _maybe_send_daily_digests() -> None:
    now = _digest_now()
    today = now.date().isoformat()
    state = _read_state()
    if state.get("last_digest_date") == today:
        return
    retry_at = str(state.get("digest_retry_not_before") or "")
    if retry_at and not _time_reached(retry_at):
        return
    hour, minute = _digest_time()
    if (now.hour, now.minute) < (hour, minute):
        return
    wait_reason = _digest_scoring_wait_reason(state, now)
    if wait_reason:
        state.update(
            {
                "message": (
                    f"Daily digest waiting for {wait_reason}; "
                    f"will send by {_format_time(_digest_latest_time())} if still running."
                ),
            }
        )
        _write_state(state)
        return
    _runtime["phase"] = "sending daily digests"
    from job_agent.digest import send_daily_job_digests

    result = send_daily_job_digests()
    attempts = int(state.get("digest_attempts") or 0) + 1
    update: dict[str, object] = {
        "digest_attempts": attempts,
        "last_digest_finished_at": _now().isoformat(),
    }
    if int(result["sent"]) > 0 or not any(
        int(result[key]) for key in ("skipped", "failures")
    ):
        update.update(
            {
                "last_digest_date": today,
                "digest_retry_not_before": "",
                "message": (
                    f"Daily digest sent to {result['sent']} subscriber(s); "
                    f"{result['skipped']} skipped; {result['failures']} failed."
                ),
            }
        )
    elif attempts < MAX_DAILY_DIGEST_ATTEMPTS:
        update.update(
            {
                "digest_retry_not_before": (
                    _now() + timedelta(seconds=DIGEST_RETRY_SECONDS)
                ).isoformat(),
                "message": (
                    f"Daily digest found no sendable matches ({result['skipped']} skipped; "
                    f"{result['failures']} failed). Retrying in 30 minutes."
                ),
            }
        )
    else:
        update.update(
            {
                "last_digest_date": today,
                "digest_retry_not_before": "",
                "message": (
                    "Daily digest stopped after three attempts because no qualifying "
                    "70+ fallback matches were available or delivery failed."
                ),
            }
        )
    state.update(update)
    if int(result["failures"]) > 0:
        _send_failure_alert_once(
            state,
            "digest_delivery_failure",
            "Daily digest delivery failure",
            (
                f"Daily digest had {result['failures']} delivery failure(s), "
                f"{result['sent']} sent, and {result['skipped']} skipped."
            ),
        )
    elif attempts >= MAX_DAILY_DIGEST_ATTEMPTS and int(result["skipped"]) > 0:
        _send_failure_alert_once(
            state,
            "digest_no_sendable_matches",
            "Daily digest had no sendable matches",
            (
                f"Daily digest stopped after {attempts} attempts with "
                f"{result['skipped']} skipped subscriber(s)."
            ),
        )
    _write_state(state)


def _today_scoring_wait_reason(now: datetime) -> str:
    from job_agent.storage import today_scoring_summary

    scoring = today_scoring_summary(now.date().isoformat())
    described = int(scoring.get("described") or 0)
    gemini_scored = int(scoring.get("gemini_scored") or 0)
    if described and gemini_scored < described:
        return f"Gemini scoring ({gemini_scored}/{described} complete)"
    return ""


def _digest_scoring_wait_reason(state: dict[str, object], now: datetime) -> str:
    if _digest_latest_time_reached(now):
        return ""
    today_wait = _today_scoring_wait_reason(now)
    if today_wait:
        return today_wait
    if state.get("description_capture_pending"):
        return "job descriptions to be captured"
    if str(state.get("gemini_not_before") or "").strip():
        return "Gemini scoring to start"
    return ""


def _gemini_submission_failure_message(
    error: Exception,
    failures: int,
    *,
    retry_label: str = "in 15 minutes",
) -> str:
    detail = str(error).replace("\n", " ")[:180]
    guidance = ""
    if "FAILED_PRECONDITION" in detail.upper():
        guidance = " Check Gemini billing and that the API is available from this server region."
    if _gemini_retry_policy(error)[0] == GEMINI_BLOCKING_RETRY_SECONDS:
        guidance = " Check the Gemini API key, billing, and prepaid credits."
    return f"Gemini submission attempt {failures}/{MAX_DAILY_BATCH_FAILURES} failed: {detail}.{guidance} Retrying {retry_label}."


def _gemini_retry_policy(error: Exception | str) -> tuple[int, str]:
    detail = str(error).upper()
    if any(
        marker in detail
        for marker in (
            "402",
            "PAYMENT REQUIRED",
            "PREPAYMENT CREDITS",
            "CREDITS ARE DEPLETED",
            "API KEY NOT VALID",
            "UNAUTHENTICATED",
        )
    ):
        return GEMINI_BLOCKING_RETRY_SECONDS, "in 6 hours after billing or credentials are fixed"
    return PRIORITY_GEMINI_RETRY_SECONDS, "in 15 minutes"


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
    return 9, 15


def _digest_latest_time() -> tuple[int, int]:
    try:
        hour, minute = DIGEST_LATEST_SEND_TIME.split(":", 1)
        parsed = int(hour), int(minute)
        if 0 <= parsed[0] <= 23 and 0 <= parsed[1] <= 59:
            return parsed
    except (TypeError, ValueError):
        pass
    return 9, 30


def _digest_latest_time_reached(now: datetime) -> bool:
    hour, minute = _digest_latest_time()
    return (now.hour, now.minute) >= (hour, minute)


def _format_time(value: tuple[int, int]) -> str:
    return f"{value[0]:02d}:{value[1]:02d}"


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
        "force_public_collection_pending": False,
        "batch_date": "",
        "batch_submissions": 0,
        "gemini_submission_failures": 0,
        "gemini_submission_state_version": GEMINI_SUBMISSION_STATE_VERSION,
        "priority_gemini_date": "",
        "priority_gemini_requests": 0,
        "priority_gemini_scored": 0,
        "priority_gemini_retry_not_before": "",
        "priority_gemini_last_error": "",
        "last_digest_date": "",
        "last_digest_finished_at": "",
        "digest_attempt_date": "",
        "digest_attempts": 0,
        "digest_retry_not_before": "",
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
