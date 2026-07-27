from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from job_agent.linkedin_review import (
    LINKEDIN_SOURCE,
    infer_role_query,
    parse_linkedin_posting_date,
)
from job_agent.models import JobPosting


CARD_SELECTOR = "[role='button'][componentkey^='job-card-component-ref-']"


def parse_linkedin_html(path: Path) -> list[JobPosting]:
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    jobs = [_parse_card(card) for card in soup.select(CARD_SELECTOR)]
    parsed_jobs = [job for job in jobs if job is not None]
    _add_selected_job_details(soup, parsed_jobs)
    return parsed_jobs


def _parse_card(card: Tag) -> JobPosting | None:
    key = str(card.get("componentkey", ""))
    job_id_match = re.search(r"(\d+)$", key)
    lines = _lines(card)
    if not job_id_match or len(lines) < 2:
        return None

    title, company = lines[:2]
    remaining = lines[2:]
    location = _first_matching(
        remaining,
        r"United States|Remote|Hybrid|On-site|,\s*[A-Z]{2}\b",
    ) or "Unknown location"
    posting_text = _first_matching(
        remaining,
        r"(?:minute|hour|day|week)s? ago|today|just now",
    )
    salary = _first_matching(remaining, r"[$€£].*(?:/yr|/year|per year)")

    return JobPosting(
        source=LINKEDIN_SOURCE,
        role_query=infer_role_query(title),
        title=title,
        company=company,
        location=location,
        posting_date=parse_linkedin_posting_date(posting_text),
        link=f"https://www.linkedin.com/jobs/view/{job_id_match.group(1)}",
        salary=salary,
        easy_apply=any(line.lower() == "easy apply" for line in remaining),
    )


def _add_selected_job_details(soup: BeautifulSoup, jobs: list[JobPosting]) -> None:
    about_node = soup.select_one("[id^='JobDetails_AboutTheJob_']")
    if about_node is None:
        return

    id_match = re.search(r"(\d+)$", str(about_node.get("id", "")))
    if not id_match:
        return

    selected = next(
        (job for job in jobs if job.link.endswith(f"/{id_match.group(1)}")),
        None,
    )
    if selected is None:
        return

    detail_root = _find_detail_root(about_node, selected.title)
    detail_lines = _lines(detail_root) if detail_root else []
    selected.salary = _first_matching(
        detail_lines,
        r"[$€£].*(?:/yr|/year|per year)",
    ) or selected.salary
    selected.workplace_type = _first_exact(
        detail_lines,
        {"on-site", "remote", "hybrid"},
    )
    selected.employment_type = _first_exact(
        detail_lines,
        {"full-time", "part-time", "contract", "temporary", "internship"},
    )
    selected.applicant_count = _first_matching(detail_lines, r"applicants?")
    selected.easy_apply = selected.easy_apply or any(
        line.lower() == "easy apply" for line in detail_lines
    )


def _find_detail_root(node: Tag, title: str) -> Tag | None:
    current: Tag | None = node
    while current is not None:
        value = current.get_text(" ", strip=True)
        if title in value and "applicant" in value.lower() and "Easy Apply" in value:
            return current
        current = current.parent if isinstance(current.parent, Tag) else None
    return None


def _lines(node: Tag) -> list[str]:
    return [
        line.strip()
        for line in node.get_text("\n", strip=True).splitlines()
        if line.strip() and line.strip() != "·"
    ]


def _first_matching(lines: list[str], pattern: str) -> str:
    return next((line for line in lines if re.search(pattern, line, re.I)), "")


def _first_exact(lines: list[str], choices: set[str]) -> str:
    return next((line for line in lines if line.lower() in choices), "")
