from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime, timezone
from functools import lru_cache
import gzip
from pathlib import Path
import re
import sqlite3

from job_agent.config import DB_PATH
from job_agent.storage import ensure_database


DOL_SOURCE_URL = "https://www.dol.gov/agencies/eta/foreign-labor/performance"
DEFAULT_DISCLOSURE_FILE = DB_PATH.parent / "reference" / "LCA_Disclosure_Data_FY2025_Q4.csv.gz"
LEGAL_SUFFIXES = {
    "CO",
    "COMPANY",
    "CORP",
    "CORPORATION",
    "INC",
    "INCORPORATED",
    "LLC",
    "LIMITED",
    "LLP",
    "LP",
}
ALIASES = {
    "AMAZON WEB SERVICES AWS": "AMAZON WEB SERVICES",
    "AWS": "AMAZON WEB SERVICES",
    "EPIC": "EPIC SYSTEMS",
    "KBR CAREERS": "KBR",
    "NVIDIA AI": "NVIDIA",
    "P G": "PROCTER GAMBLE",
}
NO_SPONSORSHIP_PATTERNS = [
    r"no (?:visa )?sponsorship",
    r"(?:will|does) not sponsor",
    r"without (?:visa )?sponsorship(?: now or in the future)?",
    r"not eligible for (?:visa )?sponsorship",
    r"u\.?s\.? citizenship (?:is )?required",
]
SPONSORSHIP_AVAILABLE_PATTERNS = [
    r"visa sponsorship (?:is )?available",
    r"(?:will|can) sponsor",
    r"sponsorship (?:will be|is) provided",
]


def analyze_visa_history(disclosure_file: Path) -> dict[str, int]:
    employer_counts = _load_employer_counts(disclosure_file)
    return _update_assessments(employer_counts)


def analyze_new_jobs(job_ids: list[int]) -> dict[str, int]:
    if not job_ids or not DEFAULT_DISCLOSURE_FILE.is_file():
        return {}
    employer_counts = _cached_employer_counts(str(DEFAULT_DISCLOSURE_FILE))
    return _update_assessments(employer_counts, job_ids)


def _update_assessments(
    employer_counts: Counter[str],
    job_ids: list[int] | None = None,
) -> dict[str, int]:
    ensure_database()
    checked_at = datetime.now(timezone.utc).isoformat()
    summary: Counter[str] = Counter()

    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        if job_ids:
            placeholders = ",".join("?" for _ in job_ids)
            rows = connection.execute(
                f"SELECT id, title, company, description FROM jobs WHERE id IN ({placeholders})",
                job_ids,
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT id, title, company, description FROM jobs"
            ).fetchall()

        for row in rows:
            assessment, evidence, filings = _assess_job(row, employer_counts)
            connection.execute(
                """
                UPDATE jobs
                SET visa_assessment = ?, visa_evidence = ?, h1b_filings = ?,
                    visa_source_url = ?, visa_checked_at = ?
                WHERE id = ?
                """,
                (
                    assessment,
                    evidence,
                    filings,
                    DOL_SOURCE_URL,
                    checked_at,
                    row["id"],
                ),
            )
            summary[assessment] += 1

        connection.commit()

    return dict(summary)


@lru_cache(maxsize=1)
def _cached_employer_counts(path: str) -> Counter[str]:
    return _load_employer_counts(Path(path))


def _load_employer_counts(path: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row.get("VISA_CLASS") != "H-1B":
                continue
            if not row.get("CASE_STATUS", "").startswith("Certified"):
                continue
            employer = normalize_employer(row.get("EMPLOYER_NAME", ""))
            if employer:
                counts[employer] += 1
    return counts


def _assess_job(
    row: sqlite3.Row,
    employer_counts: Counter[str],
) -> tuple[str, str, int]:
    description = str(row["description"] or "")
    if _matches_any(description, NO_SPONSORSHIP_PATTERNS):
        return (
            "No - explicit restriction",
            "The captured job description contains language restricting sponsorship or citizenship.",
            0,
        )
    if _matches_any(description, SPONSORSHIP_AVAILABLE_PATTERNS):
        return (
            "Yes - explicit sponsorship",
            "The captured job description explicitly indicates sponsorship is available.",
            0,
        )

    title = str(row["title"] or "").strip()
    company = str(row["company"] or "").strip()
    if not company or normalize_employer(company) == normalize_employer(title):
        return (
            "Employer missing",
            "The collected card did not contain a reliable employer name, so sponsorship history cannot be matched.",
            0,
        )

    matched_names = match_employers(company, employer_counts)
    filings = sum(employer_counts[name] for name in matched_names)
    if filings:
        names = ", ".join(matched_names[:3])
        return (
            "Potential - historical H-1B filer",
            f"Matched {filings} certified FY2025 H-1B LCA filing(s) under: {names}. This is employer history, not confirmation for this opening.",
            filings,
        )

    return (
        "Unclear - no FY2025 match",
        "No conservative employer-name match was found in the FY2025 certified H-1B LCA data. This does not prove the employer will not sponsor.",
        0,
    )


def normalize_employer(value: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", " ", value.upper()).strip()
    words = [word for word in normalized.split() if word not in LEGAL_SUFFIXES]
    result = " ".join(words)
    return ALIASES.get(result, result)


def match_employers(company: str, counts: Counter[str]) -> list[str]:
    target = normalize_employer(company)
    if not target:
        return []

    matches = [
        employer
        for employer in counts
        if employer == target
        or (
            len(target) >= 4
            and (
                employer.startswith(f"{target} ")
                or target.startswith(f"{employer} ")
            )
        )
    ]
    return sorted(matches, key=lambda name: counts[name], reverse=True)


def _matches_any(value: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, value, re.I) for pattern in patterns)
