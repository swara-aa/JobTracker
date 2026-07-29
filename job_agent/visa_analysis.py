from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime, timezone
from difflib import SequenceMatcher
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
    "ALPHABET": "GOOGLE",
    "AMAZON WEB SERVICES AWS": "AMAZON WEB SERVICES",
    "AWS": "AMAZON WEB SERVICES",
    "EPIC": "EPIC SYSTEMS",
    "FACEBOOK": "META PLATFORMS",
    "KBR CAREERS": "KBR",
    "META": "META PLATFORMS",
    "NVIDIA AI": "NVIDIA",
    "P G": "PROCTER GAMBLE",
}
NO_SPONSORSHIP_PATTERNS = [
    r"no (?:visa )?sponsorship",
    r"(?:do|does|will) not sponsor",
    r"without (?:visa )?sponsorship(?: now or in the future)?",
    r"not eligible for (?:visa )?sponsorship",
    r"u\.?s\.? citizenship (?:is )?required",
]
WORK_AUTH_WITHOUT_SPONSORSHIP_PATTERNS = [
    r"(?:must be |are |is )?(?:permanently )?authorized to work in (?:the )?(?:u\.?s\.?|united states)[\s\S]{0,160}?(?:without|no)[\s\S]{0,80}?sponsorship",
    r"work authorization that does not now or in the future require sponsorship",
    r"(?:must|require(?:s|d)?)[\s\S]{0,100}?(?:permanent|unrestricted) work authorization",
]
SPONSORSHIP_AVAILABLE_PATTERNS = [
    r"visa sponsorship (?:is )?available",
    r"(?:will|can) sponsor",
    r"sponsorship (?:will be|is) provided",
]
OPT_CPT_FRIENDLY_PATTERNS = [
    r"\b(?:opt|cpt)\b[\s\S]{0,80}\b(?:welcome|welcomed|eligible|accepted|supported|considered)\b",
    r"\b(?:f-?1|international) students?\b[\s\S]{0,80}\b(?:welcome|welcomed|eligible|accepted|supported|considered)\b",
    r"\b(?:students?|candidates?)\b[\s\S]{0,40}\b(?:on|with)\s+(?:opt|cpt)\b",
]


def analyze_visa_history(disclosure_file: Path) -> dict[str, int]:
    employer_counts = _load_employer_counts(disclosure_file)
    return _update_assessments(employer_counts)


def analyze_new_jobs(job_ids: list[int]) -> dict[str, int]:
    if not job_ids or not DEFAULT_DISCLOSURE_FILE.is_file():
        return {}
    employer_counts = _cached_employer_counts(str(DEFAULT_DISCLOSURE_FILE))
    return _update_assessments(employer_counts, job_ids)


def reassess_explicit_posting_language(
    job_ids: list[int] | None = None,
) -> dict[str, int]:
    ensure_database()
    checked_at = datetime.now(timezone.utc).isoformat()
    summary: Counter[str] = Counter()
    with sqlite3.connect(DB_PATH) as connection:
        if job_ids:
            placeholders = ",".join("?" for _ in job_ids)
            rows = connection.execute(
                f"SELECT id, description FROM jobs WHERE id IN ({placeholders})",
                job_ids,
            ).fetchall()
        else:
            rows = connection.execute("SELECT id, description FROM jobs").fetchall()
        for job_id, description in rows:
            assessment = _posting_language_assessment(str(description or ""))
            if assessment is None:
                continue
            label, evidence = assessment
            connection.execute(
                """
                UPDATE jobs
                SET visa_assessment = ?, visa_evidence = ?, h1b_filings = 0,
                    visa_source_url = '', visa_checked_at = ?
                WHERE id = ?
                """,
                (label, evidence, checked_at, job_id),
            )
            summary[label] += 1
        connection.commit()
    return dict(summary)


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
    posting_assessment = _posting_language_assessment(description)
    if posting_assessment is not None:
        return (*posting_assessment, 0)

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


def _posting_language_assessment(description: str) -> tuple[str, str] | None:
    if _matches_any(description, NO_SPONSORSHIP_PATTERNS):
        return (
            "No - explicit restriction",
            "The captured job description contains language restricting sponsorship or citizenship.",
        )
    if _matches_any(description, WORK_AUTH_WITHOUT_SPONSORSHIP_PATTERNS):
        return (
            "No - requires independent work authorization",
            "The captured job description requires work authorization that does not need present or future sponsorship.",
        )
    if _matches_any(description, OPT_CPT_FRIENDLY_PATTERNS):
        return (
            "Yes - OPT/CPT friendly",
            "The captured job description explicitly welcomes or accepts OPT/CPT or F-1 candidates. Verify the exact policy with the employer; this is not legal advice.",
        )
    if _matches_any(description, SPONSORSHIP_AVAILABLE_PATTERNS):
        return (
            "Yes - explicit sponsorship",
            "The captured job description explicitly indicates sponsorship is available.",
        )
    return None


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
    if matches:
        return sorted(matches, key=lambda name: counts[name], reverse=True)

    if len(target) < 5:
        return []
    candidates = sorted(
        (
            (SequenceMatcher(None, target, employer).ratio(), employer)
            for employer in counts
            if len(employer) >= 5
        ),
        reverse=True,
    )
    if not candidates:
        return []
    best_score, best_employer = candidates[0]
    second_score = candidates[1][0] if len(candidates) > 1 else 0.0
    if best_score >= 0.88 and best_score - second_score >= 0.04:
        return [best_employer]
    return []


def _matches_any(value: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, value, re.I) for pattern in patterns)
