from __future__ import annotations

import csv
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from job_agent.config import COMPANY_DATABASE_PATH, COMPANY_INTELLIGENCE_ENABLED


LOGGER = logging.getLogger(__name__)
REQUIRED_COLUMNS = {
    "company_name",
    "aliases",
    "fortune_500",
    "visa_friendly",
    "sponsors_h1b",
    "hires_entry_level",
    "hires_software_engineers",
    "hires_ai_ml",
    "industry",
    "careers_url",
    "notes",
}
LEGAL_SUFFIXES = {
    "CO",
    "COMPANY",
    "CORP",
    "CORPORATION",
    "INC",
    "INCORPORATED",
    "LIMITED",
    "LLC",
    "LLP",
    "LP",
    "LTD",
}


def normalize_company_name(value: str) -> str:
    """Return a comparison key without punctuation or common legal suffixes."""
    normalized = re.sub(r"[^A-Z0-9]+", " ", str(value).upper()).strip()
    words = normalized.split()
    while words and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    while words and words[0] in {"THE", "A", "AN"}:
        words.pop(0)
    return " ".join(words)


def _as_optional_bool(value: str | None, *, row_number: int, field: str) -> bool | None:
    normalized = str(value or "").strip().lower()
    if not normalized or normalized in {"unknown", "n/a", "na"}:
        return None
    if normalized in {"true", "yes", "1"}:
        return True
    if normalized in {"false", "no", "0"}:
        return False
    LOGGER.warning(
        "Ignoring invalid %s value %r in company CSV row %s; treating it as unknown.",
        field,
        value,
        row_number,
    )
    return None


@dataclass(frozen=True)
class CompanyRecord:
    company_name: str
    aliases: tuple[str, ...]
    fortune_500: bool | None
    visa_friendly: bool | None
    sponsors_h1b: bool | None
    hires_entry_level: bool | None
    hires_software_engineers: bool | None
    hires_ai_ml: bool | None
    industry: str
    careers_url: str
    notes: str

    def attributes(self, *, matched_by: str) -> dict[str, Any]:
        data = asdict(self)
        data["aliases"] = list(self.aliases)
        data["matched_by"] = matched_by
        return data


class CompanyIntelligence:
    """In-memory, CSV-backed company metadata lookup."""

    def __init__(self, path: Path | str | None = None, *, enabled: bool | None = None) -> None:
        self.path = Path(path) if path is not None else COMPANY_DATABASE_PATH
        self.enabled = COMPANY_INTELLIGENCE_ENABLED if enabled is None else enabled
        self._companies: dict[str, CompanyRecord] = {}
        self._names: dict[str, tuple[CompanyRecord, str]] = {}
        self.load()

    def load(self) -> None:
        self._companies = {}
        self._names = {}
        if not self.enabled:
            LOGGER.info("Company intelligence is disabled.")
            return
        if not self.path.exists():
            LOGGER.warning("Company database is missing at %s; companies will be treated as unknown.", self.path)
            return

        try:
            with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                fieldnames = set(reader.fieldnames or [])
                missing_columns = REQUIRED_COLUMNS.difference(fieldnames)
                if missing_columns:
                    LOGGER.warning(
                        "Company database %s is missing columns: %s. No company metadata was loaded.",
                        self.path,
                        ", ".join(sorted(missing_columns)),
                    )
                    return
                for row_number, row in enumerate(reader, start=2):
                    self._load_row(row, row_number)
        except (OSError, csv.Error) as error:
            LOGGER.warning("Could not load company database %s: %s", self.path, error)

    def _load_row(self, row: dict[str, str | None], row_number: int) -> None:
        company_name = str(row.get("company_name") or "").strip()
        canonical_key = normalize_company_name(company_name)
        if not canonical_key:
            if any(str(value or "").strip() for value in row.values()):
                LOGGER.warning("Ignoring malformed company CSV row %s: company_name is blank.", row_number)
            return
        if canonical_key in self._companies:
            LOGGER.warning(
                "Ignoring duplicate company %r in CSV row %s; the first row remains authoritative.",
                company_name,
                row_number,
            )
            return

        aliases = tuple(
            alias.strip()
            for alias in str(row.get("aliases") or "").split("|")
            if alias.strip()
        )
        record = CompanyRecord(
            company_name=company_name,
            aliases=aliases,
            fortune_500=_as_optional_bool(row.get("fortune_500"), row_number=row_number, field="fortune_500"),
            visa_friendly=_as_optional_bool(row.get("visa_friendly"), row_number=row_number, field="visa_friendly"),
            sponsors_h1b=_as_optional_bool(row.get("sponsors_h1b"), row_number=row_number, field="sponsors_h1b"),
            hires_entry_level=_as_optional_bool(row.get("hires_entry_level"), row_number=row_number, field="hires_entry_level"),
            hires_software_engineers=_as_optional_bool(row.get("hires_software_engineers"), row_number=row_number, field="hires_software_engineers"),
            hires_ai_ml=_as_optional_bool(row.get("hires_ai_ml"), row_number=row_number, field="hires_ai_ml"),
            industry=str(row.get("industry") or "").strip(),
            careers_url=str(row.get("careers_url") or "").strip(),
            notes=str(row.get("notes") or "").strip(),
        )
        self._companies[canonical_key] = record
        self._register_name(canonical_key, record, "primary")
        for alias in aliases:
            alias_key = normalize_company_name(alias)
            if alias_key:
                self._register_name(alias_key, record, "alias")

    def _register_name(self, key: str, record: CompanyRecord, matched_by: str) -> None:
        existing = self._names.get(key)
        if existing and existing[0] == record:
            return
        if existing and existing[0] != record:
            LOGGER.warning(
                "Ignoring duplicate company alias %r for %s; it already belongs to %s.",
                key,
                record.company_name,
                existing[0].company_name,
            )
            return
        self._names[key] = (record, matched_by)

    def get_company(self, company_name: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        match = self._names.get(normalize_company_name(company_name))
        if not match:
            return None
        record, matched_by = match
        return record.attributes(matched_by=matched_by)

    def is_known_company(self, company_name: str) -> bool:
        return self.get_company(company_name) is not None

    def get_company_attributes(self, company_name: str) -> dict[str, Any]:
        return self.get_company(company_name) or {"company_name": str(company_name or ""), "matched_by": "unknown"}


_COMPANY_INTELLIGENCE = CompanyIntelligence()


def initialize_company_intelligence() -> None:
    """Reload the configured CSV once while Flask is starting."""
    _COMPANY_INTELLIGENCE.load()


def get_company(company_name: str) -> dict[str, Any] | None:
    return _COMPANY_INTELLIGENCE.get_company(company_name)


def is_known_company(company_name: str) -> bool:
    return _COMPANY_INTELLIGENCE.is_known_company(company_name)


def get_company_attributes(company_name: str) -> dict[str, Any]:
    return _COMPANY_INTELLIGENCE.get_company_attributes(company_name)
