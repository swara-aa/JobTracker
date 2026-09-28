from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from job_agent.web import create_app
from job_agent.storage import analytics_summary, ensure_database


class AnalyticsTests(unittest.TestCase):
    def test_company_and_visa_totals_are_counted_for_the_selected_period(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "jobs.db"
            with patch("job_agent.storage.DB_PATH", database_path):
                ensure_database()
                collected_at = datetime.now(timezone.utc).isoformat()
                jobs = [
                    ("Acme Inc.", 12, "Potential - historical H-1B filer"),
                    ("Acme Inc.", 12, "Potential - historical H-1B filer"),
                    ("Bright Labs", 0, "Yes - explicit sponsorship"),
                    ("Clearwater", 0, "Unclear - no FY2025 match"),
                    ("Denied Co", 0, "No - explicit restriction"),
                    ("Independent Co", 0, "No - requires independent work authorization"),
                ]
                with sqlite3.connect(database_path) as connection:
                    for index, (company, h1b_filings, visa_assessment) in enumerate(jobs):
                        connection.execute(
                            """
                            INSERT INTO jobs (
                                source, role_query, title, company, location, posting_date, link,
                                h1b_filings, visa_assessment, collected_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                "test",
                                "Software Engineer",
                                f"Role {index}",
                                company,
                                "United States",
                                collected_at,
                                f"https://example.test/{index}",
                                h1b_filings,
                                visa_assessment,
                                collected_at,
                            ),
                        )

                overview = analytics_summary(7)["overview"]

            self.assertEqual(overview["jobs_added"], 6)
            self.assertEqual(overview["companies_seen"], 5)
            self.assertEqual(overview["companies_with_h1b_filings"], 1)
            self.assertEqual(overview["postings_at_h1b_filing_employers"], 2)
            self.assertEqual(overview["explicit_sponsorship_postings"], 1)
            self.assertEqual(overview["visa_unclear_or_unassessed"], 1)
            self.assertEqual(overview["no_visa_hard_no"], 4)
            self.assertEqual(overview["visa_hard_no"], 2)

    def test_analytics_queries_do_not_run_after_context_closes(self) -> None:
        class ClosingConnection(sqlite3.Connection):
            def __exit__(self, exc_type, exc_value, traceback) -> None:  # type: ignore[override]
                super().__exit__(exc_type, exc_value, traceback)
                self.close()

        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "jobs.db"
            with patch("job_agent.storage.DB_PATH", database_path):
                ensure_database()
                with patch(
                    "job_agent.storage._storage_connection",
                    lambda: sqlite3.connect(database_path, factory=ClosingConnection),
                ):
                    summary = analytics_summary(7)

            self.assertIn("locations", summary)

    def test_analytics_route_renders_with_no_recent_scores(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "jobs.db"
            with patch("job_agent.storage.DB_PATH", database_path):
                ensure_database()
                app = create_app()
                client = app.test_client()
                with client.session_transaction() as session:
                    session["authenticated"] = True

                response = client.get("/analytics")

            self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
