from __future__ import annotations

import logging

from job_agent.config import ROLE_QUERIES
from job_agent.sources import get_sources
from job_agent.storage import save_jobs_with_ids


logger = logging.getLogger(__name__)


def run_collection_job() -> int:
    return int(run_collection_and_prepare_matches()["saved"])


def run_collection_and_prepare_matches() -> dict[str, object]:
    collected_jobs = []

    for source in get_sources():
        for role_query in ROLE_QUERIES:
            try:
                jobs = list(source.fetch(role_query))
                collected_jobs.extend(jobs)
                logger.info("Fetched %s jobs from %s for %s", len(jobs), source.name, role_query)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Source %s failed for %s: %s", source.name, role_query, exc)

    saved_job_ids = save_jobs_with_ids(collected_jobs)
    local_scored = 0
    if saved_job_ids:
        from job_agent.local_scoring import score_jobs_locally

        try:
            local_scored = int(score_jobs_locally(saved_job_ids)["scored"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("New jobs were saved but local scoring failed: %s", exc)

    batch_message = "No new described jobs were collected."
    if saved_job_ids:
        batch_message = _submit_new_matches_to_gemini_batch()
    return {
        "saved": len(saved_job_ids),
        "saved_job_ids": saved_job_ids,
        "local_scored": local_scored,
        "gemini_batch_message": batch_message,
    }


def _submit_new_matches_to_gemini_batch() -> str:
    from job_agent.config import get_user_setting
    from job_agent.gemini_batch import batch_status, submit_gemini_resume_batch
    from job_agent.storage import fetch_resumes

    if not get_user_setting("GEMINI_API_KEY"):
        return "Gemini batch not submitted: GEMINI_API_KEY is not configured."
    if not fetch_resumes():
        return "Gemini batch not submitted: upload a resume first."
    if batch_status(refresh=False).get("active"):
        return "Gemini batch not submitted: another batch is already running."
    try:
        state = submit_gemini_resume_batch()
        return str(state["message"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Automatic Gemini batch submission failed: %s", exc)
        return f"Gemini batch was not submitted: {str(exc)[:180]}"
