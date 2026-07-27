from __future__ import annotations

import logging
import time

import schedule

from job_agent.collector import run_collection_job
from job_agent.config import DEFAULT_SCHEDULE_TIME
from job_agent.notifications import send_collection_digest


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)

logger = logging.getLogger(__name__)


def run_scheduler_loop() -> None:
    schedule.every().day.at(DEFAULT_SCHEDULE_TIME).do(_run_logged_job)
    logger.info("Scheduler started; daily collection time is %s", DEFAULT_SCHEDULE_TIME)

    _run_logged_job()

    while True:
        schedule.run_pending()
        time.sleep(30)


def _run_logged_job() -> None:
    saved = run_collection_job()
    try:
        send_collection_digest(saved)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Collection digest failed: %s", exc)
    logger.info("Collection finished; saved %s new jobs", saved)
