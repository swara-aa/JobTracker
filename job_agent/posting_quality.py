from __future__ import annotations

from collections import Counter
import re
from typing import Iterable


ANONYMOUS_COMPANY_PATTERN = re.compile(r"\b(confidential|undisclosed|stealth)\b", re.IGNORECASE)
MINIMUM_REPEATED_POSTINGS = 4


def verification_reasons_by_job(
    jobs: Iterable[dict[str, object]],
) -> dict[int, list[str]]:
    job_list = list(jobs)
    repeated_postings = Counter(_posting_key(job) for job in job_list)
    reasons: dict[int, list[str]] = {}

    for job in job_list:
        job_id = int(job["id"])
        title = str(job.get("title") or "").strip()
        company = str(job.get("company") or "").strip()
        job_reasons: list[str] = []
        if _normalized(title) and _normalized(title) == _normalized(company):
            job_reasons.append("Company name matches the job title")
        if ANONYMOUS_COMPANY_PATTERN.search(company):
            job_reasons.append("Employer is listed anonymously")
        if repeated_postings[_posting_key(job)] >= MINIMUM_REPEATED_POSTINGS:
            job_reasons.append("Repeated identical posting")
        if job_reasons:
            reasons[job_id] = job_reasons
    return reasons


def _posting_key(job: dict[str, object]) -> tuple[str, str, str]:
    return (
        _normalized(str(job.get("title") or "")),
        _normalized(str(job.get("company") or "")),
        _normalized(str(job.get("location") or "")),
    )


def _normalized(value: str) -> str:
    without_badges = re.sub(r"\(verified job\)", "", value, flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]+", "", without_badges.lower())
