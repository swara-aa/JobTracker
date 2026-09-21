from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from urllib.parse import parse_qs, quote, urlparse

import requests
from bs4 import BeautifulSoup

from job_agent import config


CLOSED_PATTERNS = (
    re.compile(r"\bno longer accepting applications\b", re.IGNORECASE),
    re.compile(r"\bapplications (?:are|have been) closed\b", re.IGNORECASE),
    re.compile(r"\b(?:this|the) (?:job|position|opportunity)(?: posting)? (?:is|has been) (?:closed|filled|expired|removed|no longer available)\b", re.IGNORECASE),
    re.compile(r"\bjob (?:is|was) no longer available\b", re.IGNORECASE),
    re.compile(r"\bjob posting (?:has |is )?expired\b", re.IGNORECASE),
    re.compile(r"\bposting (?:has |is )?expired\b", re.IGNORECASE),
    re.compile(r"\bposition has been filled\b", re.IGNORECASE),
    re.compile(r"\bjob you(?:'|’)re looking for (?:is )?no longer (?:open|available)\b", re.IGNORECASE),
    re.compile(r"\bjob posting you(?:'|’)re looking for no longer exists\b", re.IGNORECASE),
)
APPLY_CONTROL_PATTERN = re.compile(
    r"\b(?:apply|easy apply|submit application|start (?:an|your) application)\b",
    re.IGNORECASE,
)
GENERIC_CAREER_PATHS = {
    "",
    "careers",
    "jobs",
    "job-search",
    "search",
    "search-jobs",
}


@dataclass(frozen=True)
class ApplyReadiness:
    status: str
    reason: str
    checked_at: str
    final_url: str = ""

    @property
    def is_open(self) -> bool:
        return self.status == "open"


def validate_apply_readiness(
    job: dict[str, object],
    *,
    now: datetime | None = None,
) -> ApplyReadiness:
    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    link = str(job.get("link") or "").strip()
    parsed = urlparse(link)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return _result("unverified", "Invalid application URL", checked_at, link)

    targets = _validation_targets(link)
    last_reason = "Application page could not be verified"
    for target, authoritative in targets:
        try:
            response = requests.get(
                target,
                headers={
                    "User-Agent": config.USER_AGENT,
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
                },
                timeout=config.DIGEST_APPLY_CHECK_TIMEOUT_SECONDS,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            last_reason = f"Application page request failed: {type(exc).__name__}"
            continue

        final_url = str(response.url or link)
        if response.status_code in {404, 410}:
            return _result("closed", f"Application page returned {response.status_code}", checked_at, final_url)
        if response.status_code >= 400:
            last_reason = f"Application page returned {response.status_code}"
            continue
        if authoritative:
            if _authoritative_payload_is_open(response):
                return _result("open", "Confirmed by the employer's application system", checked_at, link)
            last_reason = "Employer application system did not return an active posting"
            continue

        page_result = _evaluate_html_page(response.text, final_url, job, checked_at)
        if page_result.status != "unverified":
            return page_result
        last_reason = page_result.reason

    return _result("unverified", last_reason, checked_at, link)


def _validation_targets(link: str) -> list[tuple[str, bool]]:
    parsed = urlparse(link)
    host = parsed.netloc.lower().split(":", 1)[0]
    path_parts = [part for part in parsed.path.split("/") if part]

    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"}:
        try:
            jobs_index = path_parts.index("jobs")
            board = path_parts[jobs_index - 1]
            job_id = path_parts[jobs_index + 1]
        except (ValueError, IndexError):
            pass
        else:
            api_url = (
                f"https://boards-api.greenhouse.io/v1/boards/{quote(board, safe='')}/jobs/"
                f"{quote(job_id, safe='')}"
            )
            return [(api_url, True), (link, False)]

    if host == "jobs.lever.co" and len(path_parts) >= 2:
        site, posting_id = path_parts[0], path_parts[1]
        api_url = (
            f"https://api.lever.co/v0/postings/{quote(site, safe='')}/"
            f"{quote(posting_id, safe='')}"
        )
        return [(api_url, True), (link, False)]

    if host.endswith("linkedin.com"):
        job_id = _linkedin_job_id(parsed)
        if job_id:
            guest_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
            return [(guest_url, False), (link, False)]

    return [(link, False)]


def _linkedin_job_id(parsed) -> str:
    match = re.search(r"/jobs/view/(?:[^/?#]*-)?(\d+)(?:/|$)", parsed.path)
    if match:
        return match.group(1)
    values = parse_qs(parsed.query).get("currentJobId", [])
    return str(values[0]) if values and str(values[0]).isdigit() else ""


def _authoritative_payload_is_open(response: requests.Response) -> bool:
    try:
        payload = response.json()
    except (requests.JSONDecodeError, json.JSONDecodeError, ValueError):
        return False
    return isinstance(payload, dict) and bool(payload.get("id")) and bool(
        payload.get("title") or payload.get("text")
    )


def _evaluate_html_page(
    html: str,
    final_url: str,
    job: dict[str, object],
    checked_at: datetime,
) -> ApplyReadiness:
    soup = BeautifulSoup(html or "", "html.parser")
    page_text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
    for pattern in CLOSED_PATTERNS:
        if pattern.search(page_text):
            return _result("closed", "Application page says the role is closed", checked_at, final_url)

    structured_posting = _structured_job_posting(soup)
    if structured_posting:
        valid_through = str(structured_posting.get("validThrough") or "").strip()
        if valid_through and _is_expired(valid_through, checked_at):
            return _result("closed", "Application deadline has passed", checked_at, final_url)
        return _result("open", "Active structured job posting found", checked_at, final_url)

    if _is_generic_career_page(final_url):
        return _result("unverified", "Application link redirected to a general careers page", checked_at, final_url)

    if not _page_matches_job(page_text, job):
        return _result("unverified", "Application page does not match the selected job", checked_at, final_url)

    if _has_apply_control(soup):
        return _result("open", "Live application control found", checked_at, final_url)

    return _result("unverified", "No active application control was found", checked_at, final_url)


def _structured_job_posting(soup: BeautifulSoup) -> dict[str, object] | None:
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        for item in _walk_json(payload):
            item_type = item.get("@type")
            types = item_type if isinstance(item_type, list) else [item_type]
            if "JobPosting" in types:
                return item
    return None


def _walk_json(value: object):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _is_expired(value: str, now: datetime) -> bool:
    normalized = value.strip().replace("Z", "+00:00")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
        normalized = f"{normalized}T23:59:59+00:00"
    try:
        expires_at = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at.astimezone(timezone.utc) < now.astimezone(timezone.utc)


def _has_apply_control(soup: BeautifulSoup) -> bool:
    for node in soup.select("a[href], button, input[type='submit'], form[action]"):
        label = " ".join(
            str(value or "")
            for value in (
                node.get_text(" ", strip=True),
                node.get("value"),
                node.get("aria-label"),
                node.get("title"),
                node.get("href"),
                node.get("action"),
            )
        )
        if APPLY_CONTROL_PATTERN.search(label):
            return True
    return False


def _page_matches_job(page_text: str, job: dict[str, object]) -> bool:
    normalized_page = _normalize(page_text)
    title = _normalize(str(job.get("title") or ""))
    return bool(title and title in normalized_page)


def _is_generic_career_page(value: str) -> bool:
    path = urlparse(value).path.strip("/").lower()
    return path in GENERIC_CAREER_PATHS


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def _result(
    status: str,
    reason: str,
    checked_at: datetime,
    final_url: str,
) -> ApplyReadiness:
    return ApplyReadiness(
        status=status,
        reason=reason,
        checked_at=checked_at.astimezone(timezone.utc).isoformat(),
        final_url=final_url,
    )
