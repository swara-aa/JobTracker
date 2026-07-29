from __future__ import annotations

import sqlite3
import logging
import json
import re
from difflib import SequenceMatcher
from typing import Iterable

from job_agent.config import DB_PATH
from job_agent.models import JobPosting


logger = logging.getLogger(__name__)


def ensure_database() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                role_query TEXT NOT NULL,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                location TEXT NOT NULL,
                posting_date TEXT NOT NULL,
                link TEXT NOT NULL,
                salary TEXT NOT NULL DEFAULT '',
                workplace_type TEXT NOT NULL DEFAULT '',
                employment_type TEXT NOT NULL DEFAULT '',
                applicant_count TEXT NOT NULL DEFAULT '',
                easy_apply INTEGER NOT NULL DEFAULT 0,
                description TEXT NOT NULL DEFAULT '',
                visa_assessment TEXT NOT NULL DEFAULT '',
                visa_evidence TEXT NOT NULL DEFAULT '',
                h1b_filings INTEGER NOT NULL DEFAULT 0,
                visa_source_url TEXT NOT NULL DEFAULT '',
                visa_checked_at TEXT NOT NULL DEFAULT '',
                gemini_status TEXT NOT NULL DEFAULT '',
                gemini_summary TEXT NOT NULL DEFAULT '',
                gemini_skills_required TEXT NOT NULL DEFAULT '[]',
                gemini_skills_preferred TEXT NOT NULL DEFAULT '[]',
                gemini_responsibilities TEXT NOT NULL DEFAULT '[]',
                gemini_requirements TEXT NOT NULL DEFAULT '[]',
                gemini_education TEXT NOT NULL DEFAULT '',
                gemini_experience TEXT NOT NULL DEFAULT '',
                gemini_location TEXT NOT NULL DEFAULT '',
                gemini_workplace_type TEXT NOT NULL DEFAULT '',
                gemini_employment_type TEXT NOT NULL DEFAULT '',
                gemini_visa_status TEXT NOT NULL DEFAULT '',
                gemini_visa_evidence TEXT NOT NULL DEFAULT '',
                gemini_error TEXT NOT NULL DEFAULT '',
                gemini_model TEXT NOT NULL DEFAULT '',
                gemini_analyzed_at TEXT NOT NULL DEFAULT '',
                application_status TEXT NOT NULL DEFAULT 'Saved',
                applied_date TEXT NOT NULL DEFAULT '',
                applied_at TEXT NOT NULL DEFAULT '',
                application_link TEXT NOT NULL DEFAULT '',
                application_notes TEXT NOT NULL DEFAULT '',
                follow_up_date TEXT NOT NULL DEFAULT '',
                local_match_score INTEGER,
                local_match_resume_id INTEGER,
                local_match_evidence TEXT NOT NULL DEFAULT '[]',
                local_match_missing TEXT NOT NULL DEFAULT '[]',
                local_match_hard_no INTEGER NOT NULL DEFAULT 0,
                local_match_hard_no_reasons TEXT NOT NULL DEFAULT '[]',
                local_match_analyzed_at TEXT NOT NULL DEFAULT '',
                local_semantic_score INTEGER,
                public_capture_metadata TEXT NOT NULL DEFAULT '{}',
                public_capture_status TEXT NOT NULL DEFAULT '',
                public_captured_at TEXT NOT NULL DEFAULT '',
                collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source, link)
            )
            """
        )
        existing_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
        }
        migrations = {
            "salary": "TEXT NOT NULL DEFAULT ''",
            "workplace_type": "TEXT NOT NULL DEFAULT ''",
            "employment_type": "TEXT NOT NULL DEFAULT ''",
            "applicant_count": "TEXT NOT NULL DEFAULT ''",
            "easy_apply": "INTEGER NOT NULL DEFAULT 0",
            "description": "TEXT NOT NULL DEFAULT ''",
            "visa_assessment": "TEXT NOT NULL DEFAULT ''",
            "visa_evidence": "TEXT NOT NULL DEFAULT ''",
            "h1b_filings": "INTEGER NOT NULL DEFAULT 0",
            "visa_source_url": "TEXT NOT NULL DEFAULT ''",
            "visa_checked_at": "TEXT NOT NULL DEFAULT ''",
            "gemini_status": "TEXT NOT NULL DEFAULT ''",
            "gemini_summary": "TEXT NOT NULL DEFAULT ''",
            "gemini_skills_required": "TEXT NOT NULL DEFAULT '[]'",
            "gemini_skills_preferred": "TEXT NOT NULL DEFAULT '[]'",
            "gemini_responsibilities": "TEXT NOT NULL DEFAULT '[]'",
            "gemini_requirements": "TEXT NOT NULL DEFAULT '[]'",
            "gemini_education": "TEXT NOT NULL DEFAULT ''",
            "gemini_experience": "TEXT NOT NULL DEFAULT ''",
            "gemini_location": "TEXT NOT NULL DEFAULT ''",
            "gemini_workplace_type": "TEXT NOT NULL DEFAULT ''",
            "gemini_employment_type": "TEXT NOT NULL DEFAULT ''",
            "gemini_visa_status": "TEXT NOT NULL DEFAULT ''",
            "gemini_visa_evidence": "TEXT NOT NULL DEFAULT ''",
            "gemini_error": "TEXT NOT NULL DEFAULT ''",
            "gemini_model": "TEXT NOT NULL DEFAULT ''",
            "gemini_analyzed_at": "TEXT NOT NULL DEFAULT ''",
            "application_status": "TEXT NOT NULL DEFAULT 'Saved'",
            "applied_date": "TEXT NOT NULL DEFAULT ''",
            "applied_at": "TEXT NOT NULL DEFAULT ''",
            "application_link": "TEXT NOT NULL DEFAULT ''",
            "application_notes": "TEXT NOT NULL DEFAULT ''",
            "follow_up_date": "TEXT NOT NULL DEFAULT ''",
            "local_match_score": "INTEGER",
            "local_match_resume_id": "INTEGER",
            "local_match_evidence": "TEXT NOT NULL DEFAULT '[]'",
            "local_match_missing": "TEXT NOT NULL DEFAULT '[]'",
            "local_match_hard_no": "INTEGER NOT NULL DEFAULT 0",
            "local_match_hard_no_reasons": "TEXT NOT NULL DEFAULT '[]'",
            "local_match_analyzed_at": "TEXT NOT NULL DEFAULT ''",
            "local_semantic_score": "INTEGER",
            "public_capture_metadata": "TEXT NOT NULL DEFAULT '{}'",
            "public_capture_status": "TEXT NOT NULL DEFAULT ''",
            "public_captured_at": "TEXT NOT NULL DEFAULT ''",
        }
        for column, definition in migrations.items():
            if column not in existing_columns:
                connection.execute(f"ALTER TABLE jobs ADD COLUMN {column} {definition}")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS resumes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                filename TEXT NOT NULL,
                content TEXT NOT NULL,
                uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS resume_job_matches (
                job_id INTEGER NOT NULL,
                resume_id INTEGER NOT NULL,
                score INTEGER NOT NULL DEFAULT 0,
                is_best INTEGER NOT NULL DEFAULT 0,
                rationale TEXT NOT NULL DEFAULT '',
                matched_skills TEXT NOT NULL DEFAULT '[]',
                missing_skills TEXT NOT NULL DEFAULT '[]',
                improvements TEXT NOT NULL DEFAULT '[]',
                hard_no INTEGER NOT NULL DEFAULT 0,
                hard_no_reasons TEXT NOT NULL DEFAULT '[]',
                analyzed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (job_id, resume_id),
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
                FOREIGN KEY (resume_id) REFERENCES resumes(id) ON DELETE CASCADE
            )
            """
        )
        existing_match_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(resume_job_matches)").fetchall()
        }
        match_migrations = {
            "hard_no": "INTEGER NOT NULL DEFAULT 0",
            "hard_no_reasons": "TEXT NOT NULL DEFAULT '[]'",
        }
        for column, definition in match_migrations.items():
            if column not in existing_match_columns:
                connection.execute(
                    f"ALTER TABLE resume_job_matches ADD COLUMN {column} {definition}"
                )
        connection.commit()


def save_jobs(jobs: Iterable[JobPosting]) -> int:
    return len(save_jobs_with_ids(jobs))


def save_jobs_with_ids(jobs: Iterable[JobPosting]) -> list[int]:
    ensure_database()
    saved = 0
    saved_job_ids: list[int] = []

    with sqlite3.connect(DB_PATH) as connection:
        existing_postings = connection.execute(
            "SELECT source, title, company, location FROM jobs"
        ).fetchall()
        for job in jobs:
            if _matches_existing_cross_source_posting(job, existing_postings):
                continue
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO jobs (
                    source, role_query, title, company, location, posting_date, link,
                    salary, workplace_type, employment_type, applicant_count,
                    easy_apply, description
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.source,
                    job.role_query,
                    job.title,
                    job.company,
                    job.location,
                    job.posting_date_iso,
                    job.link,
                    job.salary,
                    job.workplace_type,
                    job.employment_type,
                    job.applicant_count,
                    int(job.easy_apply),
                    job.description,
                ),
            )
            if cursor.rowcount:
                saved += 1
                saved_job_ids.append(int(cursor.lastrowid))
                existing_postings.append((job.source, job.title, job.company, job.location))

        connection.commit()

    if saved_job_ids:
        try:
            from job_agent.visa_analysis import analyze_new_jobs

            analyze_new_jobs(saved_job_ids)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Saved jobs but could not update visa assessments: %s", exc)

    return saved_job_ids


def _matches_existing_cross_source_posting(
    job: JobPosting, existing_postings: list[tuple[str, str, str, str]]
) -> bool:
    for source, title, company, location in existing_postings:
        if source == job.source:
            continue
        if (
            _text_similarity(job.company, company) >= 0.90
            and _text_similarity(job.title, title) >= 0.88
            and _text_similarity(job.location, location) >= 0.80
        ):
            return True
    return False


def _text_similarity(left: str, right: str) -> float:
    normalized_left = re.sub(r"[^a-z0-9]+", "", str(left).lower())
    normalized_right = re.sub(r"[^a-z0-9]+", "", str(right).lower())
    if not normalized_left or not normalized_right:
        return 0.0
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()


def job_count() -> int:
    ensure_database()
    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()
    return int(row[0])


def existing_job_links(links: Iterable[str]) -> set[str]:
    unique_links = sorted({str(link).strip() for link in links if str(link).strip()})
    if not unique_links:
        return set()
    ensure_database()
    placeholders = ", ".join("?" for _ in unique_links)
    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute(
            f"SELECT link FROM jobs WHERE link IN ({placeholders})",
            unique_links,
        ).fetchall()
    return {str(row[0]) for row in rows}


def job_ids_for_links(links: Iterable[str]) -> list[int]:
    unique_links = sorted({str(link).strip() for link in links if str(link).strip()})
    if not unique_links:
        return []
    ensure_database()
    placeholders = ", ".join("?" for _ in unique_links)
    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute(
            f"SELECT id FROM jobs WHERE link IN ({placeholders}) ORDER BY id",
            unique_links,
        ).fetchall()
    return [int(row[0]) for row in rows]


def job_ids_for_public_backfill(limit: int) -> list[int]:
    safe_limit = max(1, min(int(limit), 60))
    ensure_database()
    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute(
            """
            SELECT id
            FROM jobs
            WHERE source = 'LinkedIn Review' AND trim(description) = ''
              AND trim(public_capture_status) = ''
            ORDER BY posting_date DESC, id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return [int(row[0]) for row in rows]


def public_description_missing_count() -> int:
    ensure_database()
    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM jobs
            WHERE source = 'LinkedIn Review' AND trim(description) = ''
              AND trim(public_capture_status) = ''
            """
        ).fetchone()
    return int(row[0])


def job_ids_without_gemini_match() -> list[int]:
    ensure_database()
    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute(
            """
            SELECT jobs.id
            FROM jobs
            LEFT JOIN resume_job_matches AS matches
              ON matches.job_id = jobs.id AND matches.is_best = 1
            WHERE matches.job_id IS NULL AND jobs.application_status != 'Closed'
            ORDER BY jobs.posting_date DESC, jobs.id DESC
            """
        ).fetchall()
    return [int(row[0]) for row in rows]


def described_job_ids_without_gemini_match() -> list[int]:
    ensure_database()
    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute(
            """
            SELECT jobs.id
            FROM jobs
            LEFT JOIN resume_job_matches AS matches
              ON matches.job_id = jobs.id AND matches.is_best = 1
            WHERE matches.job_id IS NULL
              AND jobs.application_status != 'Closed'
              AND trim(jobs.description) != ''
            ORDER BY jobs.posting_date DESC, jobs.id DESC
            """
        ).fetchall()
    return [int(row[0]) for row in rows]


def save_linkedin_descriptions(items: Iterable[dict[str, str]]) -> list[int]:
    ensure_database()
    updated_job_ids: list[int] = []
    with sqlite3.connect(DB_PATH) as connection:
        for item in items:
            link = str(item.get("link") or "").strip()
            description = str(item.get("description") or "").strip()[:50000]
            if not link or len(description) < 80:
                continue
            cursor = connection.execute(
                """
                UPDATE jobs
                SET description = ?
                WHERE source = 'LinkedIn Review' AND link = ?
                  AND trim(description) = ''
                """,
                (description, link),
            )
            if cursor.rowcount:
                row = connection.execute(
                    "SELECT id FROM jobs WHERE source = 'LinkedIn Review' AND link = ?",
                    (link,),
                ).fetchone()
                if row:
                    updated_job_ids.append(int(row[0]))
        connection.commit()
    return updated_job_ids


def save_linkedin_public_capture(link: str, description: str, metadata: dict[str, object]) -> list[int]:
    ensure_database()
    normalized_link = str(link or "").strip()
    normalized_description = str(description or "").strip()[:50000]
    if not normalized_link:
        return []
    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute(
            "SELECT id, description FROM jobs WHERE source = 'LinkedIn Review' AND link = ?",
            (normalized_link,),
        ).fetchone()
        if row is None:
            return []
        connection.execute(
            """
            UPDATE jobs
            SET description = CASE WHEN trim(description) = '' THEN ? ELSE description END,
                public_capture_metadata = ?, public_capture_status = ?,
                public_captured_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                normalized_description,
                json.dumps(metadata),
                "captured" if normalized_description else "public_page_unavailable",
                int(row[0]),
            ),
        )
        connection.commit()
    return [int(row[0])] if normalized_description and not str(row[1]).strip() else []


def fetch_jobs(
    role: str = "",
    location: str = "",
    company: str = "",
    visa: str = "",
    application_status: str = "",
) -> list[dict[str, str]]:
    ensure_database()
    query = """
        SELECT id, source, role_query, title, company, location, posting_date, link,
               salary, workplace_type, employment_type, applicant_count,
               easy_apply, description, visa_assessment, visa_evidence,
               h1b_filings, visa_source_url, visa_checked_at, gemini_status,
               gemini_summary, gemini_visa_status, gemini_error,
               application_status, applied_date, application_link, application_notes,
               follow_up_date, applied_at,
               local_match_score, local_match_resume_id, local_match_evidence,
               local_match_missing, local_match_hard_no, local_match_hard_no_reasons,
               local_match_analyzed_at,
               matches.score AS resume_match_score,
               matches.hard_no AS resume_match_hard_no,
               matches.hard_no_reasons AS resume_match_hard_no_reasons
        FROM jobs
        LEFT JOIN resume_job_matches AS matches
          ON matches.job_id = jobs.id AND matches.is_best = 1
        WHERE (? = '' OR jobs.role_query = ?)
          AND (? = '' OR lower(jobs.location) LIKE '%' || lower(?) || '%')
          AND (? = '' OR lower(jobs.company) LIKE '%' || lower(?) || '%')
          AND (? = '' OR jobs.visa_assessment = ?)
          AND (? = '' OR jobs.application_status = ?)
        ORDER BY jobs.posting_date DESC
    """

    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            query,
            (
                role,
                role,
                location,
                location,
                company,
                company,
                visa,
                visa,
                application_status,
                application_status,
            ),
        ).fetchall()

    return [dict(row) for row in rows]


def fetch_job(job_id: int) -> dict[str, object] | None:
    ensure_database()
    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def distinct_values(column: str) -> list[str]:
    if column not in {
        "role_query",
        "location",
        "company",
        "visa_assessment",
        "application_status",
    }:
        raise ValueError(f"Unsupported column: {column}")

    ensure_database()
    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute(
            f"""
            SELECT DISTINCT {column}
            FROM jobs
            WHERE trim({column}) <> ''
            ORDER BY {column} ASC
            """
        ).fetchall()

    return [row[0] for row in rows]


def update_job_pipeline(
    job_id: int,
    application_status: str,
    applied_date: str,
    application_link: str,
    application_notes: str,
    follow_up_date: str,
) -> bool:
    ensure_database()
    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.execute(
            """
            UPDATE jobs
            SET application_status = ?, applied_date = ?, application_link = ?,
                application_notes = ?, follow_up_date = ?,
                applied_at = CASE
                    WHEN ? = 'Applied' AND trim(applied_at) = '' THEN CURRENT_TIMESTAMP
                    WHEN ? <> 'Applied' THEN ''
                    ELSE applied_at
                END
            WHERE id = ?
            """,
            (
                application_status,
                applied_date,
                application_link,
                application_notes,
                follow_up_date,
                application_status,
                application_status,
                job_id,
            ),
        )
        connection.commit()
    return bool(cursor.rowcount)


def skill_gap_summary(limit: int = 20) -> list[dict[str, object]]:
    ensure_database()
    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute(
            """
            SELECT missing_skills
            FROM resume_job_matches
            WHERE is_best = 1
            ORDER BY analyzed_at DESC
            LIMIT ?
            """,
            (max(1, limit),),
        ).fetchall()
    counts: dict[str, int] = {}
    for (encoded_skills,) in rows:
        try:
            skills = json.loads(encoded_skills)
        except (TypeError, json.JSONDecodeError):
            continue
        for skill in skills if isinstance(skills, list) else []:
            name = str(skill).strip()
            if name:
                counts[name] = counts.get(name, 0) + 1
    return [
        {"skill": skill, "count": count}
        for skill, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:10]
    ]


def fetch_resumes() -> list[dict[str, object]]:
    ensure_database()
    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, name, filename, content, uploaded_at FROM resumes ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


def save_resume(name: str, filename: str, content: str) -> int:
    ensure_database()
    with sqlite3.connect(DB_PATH) as connection:
        count = int(connection.execute("SELECT COUNT(*) FROM resumes").fetchone()[0])
        if count >= 4:
            raise ValueError("The resume library already contains four resumes.")
        cursor = connection.execute(
            "INSERT INTO resumes (name, filename, content) VALUES (?, ?, ?)",
            (name, filename, content),
        )
        connection.commit()
        return int(cursor.lastrowid)


def delete_resume(resume_id: int) -> bool:
    ensure_database()
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            "DELETE FROM resume_job_matches WHERE resume_id = ?", (resume_id,)
        )
        cursor = connection.execute("DELETE FROM resumes WHERE id = ?", (resume_id,))
        connection.commit()
    return bool(cursor.rowcount)


def fetch_resume_matches(job_id: int) -> list[dict[str, object]]:
    ensure_database()
    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT m.*, r.name, r.filename
            FROM resume_job_matches m
            JOIN resumes r ON r.id = m.resume_id
            WHERE m.job_id = ?
            ORDER BY m.score DESC, r.name ASC
            """,
            (job_id,),
        ).fetchall()
    return [dict(row) for row in rows]
