from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
import email.utils
from typing import Iterable
from urllib.parse import quote
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

from job_agent import config
from job_agent.filters import (
    is_entry_level,
    looks_like_usa_location,
    matches_role_query,
    posted_within_last_7_days,
)
from job_agent.models import JobPosting


class BaseSource(ABC):
    name: str

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": config.USER_AGENT})

    @abstractmethod
    def fetch(self, role_query: str) -> Iterable[JobPosting]:
        raise NotImplementedError

    def _keep(self, role_query: str, title: str, location: str, posted_at: datetime) -> bool:
        return (
            matches_role_query(title, role_query)
            and is_entry_level(title)
            and looks_like_usa_location(location)
            and posted_within_last_7_days(posted_at)
        )


class RemoteOkSource(BaseSource):
    name = "RemoteOK"
    base_url = "https://remoteok.com/remote-{query}-jobs.rss"

    def fetch(self, role_query: str) -> Iterable[JobPosting]:
        slug = role_query.lower().replace("/", "-").replace(" ", "-")
        response = self.session.get(
            self.base_url.format(query=quote(slug)),
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        root = ET.fromstring(response.content)
        items = root.findall(".//item")

        for item in items:
            title = _xml_text(item, "title")
            link = _xml_text(item, "link")
            company = _xml_text(item, "author") or "Unknown"
            location = _extract_remote_ok_location(_xml_text(item, "description"))
            posted_at = _parse_rfc2822(_xml_text(item, "pubDate"))

            if not self._keep(role_query, title, location, posted_at):
                continue

            yield JobPosting(
                source=self.name,
                role_query=role_query,
                title=title,
                company=company,
                location=location,
                posting_date=posted_at,
                link=link,
            )


class WeWorkRemotelySource(BaseSource):
    name = "WeWorkRemotely"
    search_url = "https://weworkremotely.com/remote-jobs/search?term={query}"

    def fetch(self, role_query: str) -> Iterable[JobPosting]:
        response = self.session.get(
            self.search_url.format(query=quote(role_query)),
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        for listing in soup.select("section.jobs article li"):
            anchor = listing.select_one("a[href]")
            if anchor is None:
                continue

            title_node = listing.select_one(".title")
            company_node = listing.select_one(".company")
            region_node = listing.select_one(".region")
            time_node = listing.select_one("time[datetime]")

            if not title_node or not company_node or not region_node or not time_node:
                continue

            title = title_node.get_text(strip=True)
            company = company_node.get_text(strip=True)
            location = region_node.get_text(strip=True)
            posted_at = _parse_iso_datetime(time_node["datetime"])
            link = f"https://weworkremotely.com{anchor['href']}"

            if not self._keep(role_query, title, location, posted_at):
                continue

            yield JobPosting(
                source=self.name,
                role_query=role_query,
                title=title,
                company=company,
                location=location,
                posting_date=posted_at,
                link=link,
            )


class RemotiveSource(BaseSource):
    name = "Remotive"
    api_url = "https://remotive.com/api/remote-jobs?search={query}"

    def fetch(self, role_query: str) -> Iterable[JobPosting]:
        response = self.session.get(
            self.api_url.format(query=quote(role_query)),
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()

        for item in payload.get("jobs", []):
            title = item.get("title", "").strip()
            company = item.get("company_name", "").strip() or "Unknown"
            location = item.get("candidate_required_location", "").strip()
            link = item.get("url", "").strip()
            published_at = item.get("publication_date", "").strip()

            if not all([title, location, link, published_at]):
                continue

            posted_at = _parse_iso_datetime(published_at)

            if not self._keep(role_query, title, location, posted_at):
                continue

            yield JobPosting(
                source=self.name,
                role_query=role_query,
                title=title,
                company=company,
                location=location,
                posting_date=posted_at,
                link=link,
            )


class GreenhouseSource(BaseSource):
    name = "Greenhouse"

    def __init__(self, boards: list[tuple[str, str]]) -> None:
        super().__init__()
        self.boards = boards

    def fetch(self, role_query: str) -> Iterable[JobPosting]:
        for board, company in self.boards:
            response = self.session.get(
                f"https://boards-api.greenhouse.io/v1/boards/{quote(board)}/jobs?content=true",
                timeout=config.REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            for item in response.json().get("jobs", []):
                title = str(item.get("title") or "").strip()
                location = str((item.get("location") or {}).get("name") or "").strip()
                link = str(item.get("absolute_url") or "").strip()
                updated_at = str(item.get("updated_at") or "").strip()
                description = _html_to_text(str(item.get("content") or ""))
                if not all([title, location, link, updated_at]):
                    continue
                posted_at = _parse_iso_datetime(updated_at)
                if self._keep(role_query, title, location, posted_at):
                    yield JobPosting(
                        self.name,
                        role_query,
                        title,
                        company,
                        location,
                        posted_at,
                        link,
                        description=description,
                    )


class LeverSource(BaseSource):
    name = "Lever"

    def __init__(self, sites: list[tuple[str, str]]) -> None:
        super().__init__()
        self.sites = sites

    def fetch(self, role_query: str) -> Iterable[JobPosting]:
        for site, company in self.sites:
            response = self.session.get(
                f"https://api.lever.co/v0/postings/{quote(site)}?mode=json",
                timeout=config.REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            for item in response.json():
                title = str(item.get("text") or "").strip()
                location = str((item.get("categories") or {}).get("location") or "").strip()
                link = str(item.get("hostedUrl") or "").strip()
                created_ms = item.get("createdAt")
                description = str(item.get("descriptionPlain") or "").strip()
                if not description:
                    description = _html_to_text(str(item.get("description") or ""))
                if not all([title, location, link, created_ms]):
                    continue
                posted_at = datetime.fromtimestamp(int(created_ms) / 1000, tz=timezone.utc)
                if self._keep(role_query, title, location, posted_at):
                    yield JobPosting(
                        self.name,
                        role_query,
                        title,
                        company,
                        location,
                        posted_at,
                        link,
                        workplace_type="Remote" if "remote" in location.lower() else "",
                        description=description,
                    )


def get_sources() -> list[BaseSource]:
    sources: list[BaseSource] = [
        RemoteOkSource(),
        WeWorkRemotelySource(),
        RemotiveSource(),
    ]
    greenhouse_boards = config.configured_boards(config.GREENHOUSE_BOARDS)
    lever_sites = config.configured_boards(config.LEVER_SITES)
    if greenhouse_boards:
        sources.append(GreenhouseSource(greenhouse_boards))
    if lever_sites:
        sources.append(LeverSource(lever_sites))
    return sources


def _xml_text(node: ET.Element, tag: str) -> str:
    child = node.find(tag)
    return child.text.strip() if child is not None and child.text else ""


def _parse_rfc2822(value: str) -> datetime:
    parsed = email.utils.parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_iso_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _extract_remote_ok_location(description_html: str) -> str:
    soup = BeautifulSoup(description_html, "html.parser")
    text = soup.get_text(" ", strip=True)

    for candidate in [
        "United States",
        "USA Only",
        "US",
        "U.S.",
        "North America",
    ]:
        if candidate.lower() in text.lower():
            return candidate

    return text[:120] or "Unknown"


def _html_to_text(value: str) -> str:
    return BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
