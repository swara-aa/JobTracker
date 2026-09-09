from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from job_agent.models import JobPosting
from job_agent.storage import distinct_values, ensure_database, fetch_jobs, save_jobs


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
