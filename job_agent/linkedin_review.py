from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any

from job_agent.classification import infer_role_family
from job_agent.filters import normalize_text, utc_now
from job_agent.models import JobPosting


LINKEDIN_SOURCE = "LinkedIn Review"


def parse_linkedin_json(payload: str) -> list[JobPosting]:
    raw = json.loads(payload)
    if isinstance(raw, dict):
        items = [raw]
    elif isinstance(raw, list):
        items = raw
    else:
        raise ValueError("Expected a JSON object or array.")

    jobs: list[JobPosting] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        title = _clean(item.get("title")) or "LinkedIn job"
        company = _clean(item.get("company")) or "Unknown company"
        location = _clean(item.get("location")) or "Unknown location"
        link = _clean(item.get("link"))
        posting_date_text = _clean(
            item.get("posting_date")
            or item.get("posting_date_text")
            or item.get("posted")
        )
        role_query = _clean(item.get("role_query")) or infer_role_query(title)

        if not link:
            continue

        posting_date = parse_linkedin_posting_date(posting_date_text)

        jobs.append(
            JobPosting(
                source=LINKEDIN_SOURCE,
                role_query=role_query,
                title=title,
                company=company,
                location=location,
                posting_date=posting_date,
                link=link,
            )
        )

    return jobs


def build_manual_job(
    title: str,
    company: str,
    location: str,
    link: str,
    posting_date_text: str,
    role_query: str,
) -> JobPosting:
    normalized_role = role_query.strip() or infer_role_query(title)
    return JobPosting(
        source=LINKEDIN_SOURCE,
        role_query=normalized_role,
        title=title.strip(),
        company=company.strip(),
        location=location.strip(),
        posting_date=parse_linkedin_posting_date(posting_date_text),
        link=link.strip(),
    )


def infer_role_query(title: str) -> str:
    return infer_role_family(title)


def parse_linkedin_posting_date(value: str) -> datetime:
    text = normalize_text(value)
    now = utc_now()

    if not text:
        return now

    text = text.removeprefix("reposted ")
    text = text.removeprefix("posted ")

    if text in {"today", "just now"}:
        return now
    if text == "yesterday":
        return now - timedelta(days=1)

    match = re.search(
        r"(?P<count>\d+)\s+(?P<unit>minute|minutes|hour|hours|day|days|week|weeks)",
        text,
    )
    if match:
        count = int(match.group("count"))
        unit = match.group("unit")
        if unit.startswith("minute"):
            return now - timedelta(minutes=count)
        if unit.startswith("hour"):
            return now - timedelta(hours=count)
        if unit.startswith("day"):
            return now - timedelta(days=count)
        if unit.startswith("week"):
            return now - timedelta(weeks=count)

    iso_candidate = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_candidate)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return now


def linkedin_console_snippet() -> str:
    return _linkedin_capture_script("copy")


def linkedin_bookmarklet() -> str:
    script = _linkedin_capture_script("open")
    return "javascript:" + re.sub(r"\s+", " ", script).strip()


def _linkedin_capture_script(action: str) -> str:
    destination = "http://127.0.0.1:5000/linkedin-review"
    finish = (
        f'window.open("{destination}#capture=" + encodeURIComponent(JSON.stringify(cards)), "_blank");'
        if action == "open"
        else 'copy(JSON.stringify(cards, null, 2)); console.log(`Copied ${cards.length} job(s) to clipboard.`);'
    )
    script = r"""(() => {
  const text = (root, selectors) => {
    for (const selector of selectors) {
      const node = root.querySelector(selector);
      if (node && node.textContent.trim()) return node.textContent.trim();
    }
    return "";
  };

  const href = (root, selectors) => {
    for (const selector of selectors) {
      const node = root.querySelector(selector);
      if (node && node.href) return node.href;
    }
    return "";
  };

  const clean = (value) => (value || "").replace(/\s+/g, " ").trim();
  const anchors = [...document.querySelectorAll("a[href*='/jobs/view/']")];
  const cards = anchors
    .map((jobLink) => {
      const card = jobLink.closest("li, article, [role='listitem'], [data-job-id]")
        || jobLink.parentElement?.parentElement?.parentElement;
      if (!card) return null;

      const lines = (card.innerText || "")
        .split("\n")
        .map(clean)
        .filter(Boolean);
      const title = clean(jobLink.getAttribute("aria-label"))
        || clean(jobLink.getAttribute("title"))
        || clean(jobLink.innerText)
        || text(card, ["strong"]);
      const titleIndex = lines.findIndex((line) => line === title || line.includes(title));
      const details = lines.slice(titleIndex >= 0 ? titleIndex + 1 : 0);
      const company = text(card, [
        ".artdeco-entity-lockup__subtitle",
        ".job-card-container__company-name",
        ".job-card-container__primary-description"
      ]) || details[0] || "";
      const location = text(card, [
        ".artdeco-entity-lockup__caption",
        ".job-card-container__metadata-item",
        ".job-card-container__secondary-description"
      ]) || details.find((line) => /United States|Remote|Hybrid|On-site|,\s*[A-Z]{2}\b/i.test(line)) || details[1] || "";
      const postingDate = text(card, ["time", ".job-search-card__listdate"])
        || lines.find((line) => /(?:minute|hour|day|week)s? ago|today|just now/i.test(line))
        || "";

      return {
        title,
        company,
        location,
        posting_date_text: postingDate,
        link: jobLink.href,
        search_url: window.location.href,
        role_query: ""
      };
    })
    .filter(Boolean)
    .filter((job) => job.title && job.company && job.link)
    .map((job) => ({ ...job, link: job.link.split("?")[0] }))
    .filter((job, index, all) => all.findIndex((other) => other.link === job.link) === index)
    .slice(0, 50);

  if (!cards.length) {
    alert("No LinkedIn job cards were found. Open a LinkedIn Jobs results page and try again.");
    return;
  }
  __FINISH__
})();"""
    return script.replace("__FINISH__", finish)


def _clean(value: Any) -> str:
    return str(value or "").strip()
