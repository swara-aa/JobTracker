from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from job_agent.models import JobPosting
from job_agent.storage import (
    distinct_values,
    ensure_database,
    fetch_job,
    fetch_jobs,
    save_jobs,
    save_jobs_with_ids,
    today_scoring_summary,
    update_job_pipeline,
)


class StorageClassificationTests(unittest.TestCase):
    def test_imported_jobs_are_classified_by_title(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "jobs.db"
            with (
                patch("job_agent.storage.DB_PATH", database_path),
                patch("job_agent.database.DB_PATH", database_path),
            ):
                save_jobs(
                    [
                        JobPosting(
                            source="test",
                            role_query="",
                            title="Financial Analyst",
                            company="Example",
                            location="San Francisco, CA",
                            posting_date=datetime.now(timezone.utc),
                            link="https://example.test/finance",
                        )
                    ]
                )

                jobs = fetch_jobs(role="Finance & Accounting")

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["role_query"], "Finance & Accounting")

    def test_location_filter_matches_city_and_state_group(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "jobs.db"
            with (
                patch("job_agent.storage.DB_PATH", database_path),
                patch("job_agent.database.DB_PATH", database_path),
            ):
                save_jobs(
                    [
                        JobPosting(
                            source="test",
                            role_query="",
                            title="Product Manager",
                            company="Example",
                            location="San Francisco, CA",
                            posting_date=datetime.now(timezone.utc),
                            link="https://example.test/product",
                        )
                    ]
                )

                state_jobs = fetch_jobs(location="California")
                city_jobs = fetch_jobs(location="San Francisco")
                location_options = distinct_values("location")

        self.assertEqual(len(state_jobs), 1)
        self.assertEqual(len(city_jobs), 1)
        self.assertIn("California", location_options)

    def test_legacy_software_default_is_reclassified_on_startup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "jobs.db"
            with (
                patch("job_agent.storage.DB_PATH", database_path),
                patch("job_agent.database.DB_PATH", database_path),
            ):
                ensure_database()
                with database_path.open("rb"):
                    pass
                import sqlite3

                with sqlite3.connect(database_path) as connection:
                    connection.execute(
                        """
                        INSERT INTO jobs (
                            source, role_query, title, company, location, posting_date, link
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "test",
                            "Software Engineer",
                            "Marketing Coordinator",
                            "Example",
                            "Los Angeles, CA",
                            datetime.now(timezone.utc).isoformat(),
                            "https://example.test/marketing",
                        ),
                    )
                    connection.commit()

                ensure_database()
                jobs = fetch_jobs(role="Marketing & Communications")

        self.assertEqual(len(jobs), 1)

    def test_new_jobs_store_first_seen_and_source_posted_dates(self) -> None:
        posted_at = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "jobs.db"
            with (
                patch("job_agent.storage.DB_PATH", database_path),
                patch("job_agent.database.DB_PATH", database_path),
            ):
                save_jobs(
                    [
                        JobPosting(
                            source="Greenhouse",
                            role_query="Software Engineering",
                            title="Software Engineer",
                            company="Example",
                            location="San Francisco, CA",
                            posting_date=posted_at,
                            link="https://example.test/software",
                        )
                    ]
                )
                jobs = fetch_jobs()

        self.assertEqual(jobs[0]["source_posted_at"], posted_at.isoformat())
        self.assertTrue(str(jobs[0]["first_seen_at"]))

    def test_today_scoring_summary_counts_saved_jobs(self) -> None:
        posted_at = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "jobs.db"
            with (
                patch("job_agent.storage.DB_PATH", database_path),
                patch("job_agent.database.DB_PATH", database_path),
            ):
                save_jobs(
                    [
                        JobPosting(
                            source="Greenhouse",
                            role_query="Software Engineering",
                            title="Software Engineer",
                            company="Example",
                            location="San Francisco, CA",
                            posting_date=posted_at,
                            link="https://example.test/today-software",
                            description="Build Python services.",
                        )
                    ]
                )
                summary = today_scoring_summary(posted_at.date().isoformat())

        self.assertEqual(summary["collected"], 1)
        self.assertEqual(summary["described"], 1)
        self.assertEqual(summary["public_sources"], 1)
        self.assertEqual(summary["source_posted_today"], 1)

    def test_applied_pipeline_update_sets_applied_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "jobs.db"
            with (
                patch("job_agent.storage.DB_PATH", database_path),
                patch("job_agent.database.DB_PATH", database_path),
            ):
                saved_ids = save_jobs_with_ids(
                    [
                        JobPosting(
                            source="test",
                            role_query="Software Engineering",
                            title="Software Engineer",
                            company="Example",
                            location="San Francisco, CA",
                            posting_date=datetime.now(timezone.utc),
                            link="https://example.test/applied",
                            description="Build software.",
                        )
                    ]
                )
                updated = update_job_pipeline(
                    saved_ids[0],
                    "Applied",
                    "2026-09-14",
                    "https://example.test/apply",
                    "Submitted online.",
                    "",
                )
                job = fetch_job(saved_ids[0])

        self.assertTrue(updated)
        self.assertEqual(job["application_status"], "Applied")
        self.assertEqual(job["applied_date"], "2026-09-14")
        self.assertTrue(str(job["applied_at"]).strip())

    def test_gemini_scored_filter_hides_unscored_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "jobs.db"
            with (
                patch("job_agent.storage.DB_PATH", database_path),
                patch("job_agent.database.DB_PATH", database_path),
            ):
                saved_ids = save_jobs_with_ids(
                    [
                        JobPosting(
                            source="test",
                            role_query="Software Engineering",
                            title="Scored Software Engineer",
                            company="Example",
                            location="San Francisco, CA",
                            posting_date=datetime.now(timezone.utc),
                            link="https://example.test/scored",
                        ),
                        JobPosting(
                            source="test",
                            role_query="Software Engineering",
                            title="Unscored Software Engineer",
                            company="Example",
                            location="San Francisco, CA",
                            posting_date=datetime.now(timezone.utc),
                            link="https://example.test/unscored",
                        ),
                    ]
                )
                with sqlite3.connect(database_path) as connection:
                    resume_id = connection.execute(
                        """
                        INSERT INTO resumes (name, filename, content)
                        VALUES (?, ?, ?)
                        """,
                        ("Resume", "resume.txt", "Python developer"),
                    ).lastrowid
                    connection.execute(
                        """
                        INSERT INTO resume_job_matches (job_id, resume_id, score, is_best)
                        VALUES (?, ?, ?, ?)
                        """,
                        (saved_ids[0], resume_id, 92, 1),
                    )
                    connection.commit()

                jobs = fetch_jobs(gemini_scored_only=True)

        self.assertEqual([job["id"] for job in jobs], [saved_ids[0]])

    def test_reposted_same_company_title_location_is_deduped(self) -> None:
        posted_at = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "jobs.db"
            with (
                patch("job_agent.storage.DB_PATH", database_path),
                patch("job_agent.database.DB_PATH", database_path),
            ):
                saved = save_jobs(
                    [
                        JobPosting(
                            source="Greenhouse",
                            role_query="Software Engineering",
                            title="Software Engineer",
                            company="Example Corp",
                            location="San Francisco, CA",
                            posting_date=posted_at,
                            link="https://example.test/company-job",
                        ),
                        JobPosting(
                            source="LinkedIn Review",
                            role_query="Software Engineering",
                            title="Software Engineer",
                            company="Example Corporation",
                            location="California",
                            posting_date=posted_at,
                            link="https://linkedin.example.test/repost",
                        ),
                    ]
                )
                jobs = fetch_jobs()

        self.assertEqual(saved, 1)
        self.assertEqual(len(jobs), 1)


if __name__ == "__main__":
    unittest.main()
