from __future__ import annotations

import logging

from job_agent.config import ROLE_QUERIES
from job_agent.sources import get_sources
from job_agent.storage import save_jobs


logger = logging.getLogger(__name__)


def run_collection_job() -> int:
    collected_jobs = []

    for source in get_sources():
        for role_query in ROLE_QUERIES:
            try:
                jobs = list(source.fetch(role_query))
                collected_jobs.extend(jobs)
                logger.info("Fetched %s jobs from %s for %s", len(jobs), source.name, role_query)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Source %s failed for %s: %s", source.name, role_query, exc)

    return save_jobs(collected_jobs)
