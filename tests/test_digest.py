from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from job_agent.digest import active_digest_subscribers, subscribe_to_digest, top_digest_matches
from job_agent.models import JobPosting
from job_agent.storage import save_jobs


RESUME_TEXT = (
    "Marketing coordinator with campaign analytics, content strategy, social media reporting, "
    "email campaign planning, stakeholder communication, and project coordination experience. "
    "Built weekly dashboards, wrote campaign briefs, tracked conversion metrics, and worked "
    "with design and sales teams to improve campaign performance."
)


class DigestTests(unittest.TestCase):
    def test_subscribe_stores_preferences_and_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "jobs.db"
            with patch("job_agent.storage.DB_PATH", database_path), patch("job_agent.digest.DB_PATH", database_path):
                subscribe_to_digest(
                    email="PERSON@example.com",
                    name="Person",
                    roles=["Marketing & Communications"],
                    location="California",
                    resume_filename="resume.txt",
                    resume_content=RESUME_TEXT,
                )

                subscribers = active_digest_subscribers()

        self.assertEqual(len(subscribers), 1)
        self.assertEqual(subscribers[0]["email"], "person@example.com")
        self.assertEqual(subscribers[0]["roles"], ["Marketing & Communications"])

    def test_digest_matches_new_jobs_by_role_location_and_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "jobs.db"
            with (
                patch("job_agent.storage.DB_PATH", database_path),
                patch("job_agent.digest.DB_PATH", database_path),
                patch("job_agent.digest.get_user_setting", return_value=""),
            ):
                subscriber = subscribe_to_digest(
                    email="person@example.com",
                    name="Person",
                    roles=["Marketing & Communications"],
                    location="California",
                    resume_filename="resume.txt",
                    resume_content=RESUME_TEXT,
                )
                save_jobs(
                    [
                        JobPosting(
                            source="test",
                            role_query="Marketing & Communications",
                            title="Marketing Coordinator",
                            company="Example",
                            location="San Francisco, CA",
                            posting_date=datetime.now(timezone.utc),
                            link="https://example.test/marketing",
                            description="Create content strategy, campaign analytics, and social media reporting.",
                        ),
                        JobPosting(
                            source="test",
                            role_query="Finance & Accounting",
                            title="Accountant",
                            company="Other",
                            location="San Francisco, CA",
                            posting_date=datetime.now(timezone.utc),
                            link="https://example.test/accounting",
                            description="Prepare month-end close and financial statements.",
                        ),
                    ]
                )
                subscriber_record = active_digest_subscribers()[0] | subscriber
                matches = top_digest_matches(subscriber_record)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["title"], "Marketing Coordinator")


if __name__ == "__main__":
    unittest.main()
