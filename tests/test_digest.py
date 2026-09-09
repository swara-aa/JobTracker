from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from job_agent.digest import (
    _plain_digest,
    active_digest_subscribers,
    subscribe_to_digest,
    top_digest_matches,
)
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
            with (
                patch("job_agent.storage.DB_PATH", database_path),
                patch("job_agent.digest.DB_PATH", database_path),
                patch("job_agent.database.DB_PATH", database_path),
            ):
                subscribe_to_digest(
                    email="PERSON@example.com",
                    name="Person",
                    roles=["Marketing & Communications"],
                    location="California",
                    resume_filename="resume.txt",
                    resume_content=RESUME_TEXT,
                    plan="pro",
                )

                subscribers = active_digest_subscribers()

        self.assertEqual(len(subscribers), 1)
        self.assertEqual(subscribers[0]["email"], "person@example.com")
        self.assertEqual(subscribers[0]["roles"], ["Marketing & Communications"])
        self.assertEqual(subscribers[0]["plan"], "pro")

    def test_digest_matches_new_jobs_by_role_location_and_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "jobs.db"
            with (
                patch("job_agent.storage.DB_PATH", database_path),
                patch("job_agent.digest.DB_PATH", database_path),
                patch("job_agent.database.DB_PATH", database_path),
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

    def test_plain_digest_includes_rich_match_context(self) -> None:
        body = _plain_digest(
            {"name": "Person", "plan": "pro"},
            [
                {
                    "title": "Marketing Coordinator",
                    "company": "Example",
                    "score": 82,
                    "score_source": "local",
                    "role_query": "Marketing & Communications",
                    "location": "San Francisco, CA",
                    "employment_type": "Full-time",
                    "workplace_type": "Hybrid",
                    "salary": "$70,000",
                    "posting_date": "2026-09-08",
                    "source": "LinkedIn",
                    "rationale": "Strong campaign analytics overlap.",
                    "matched_skills": ["Campaign Analytics", "Content Strategy"],
                    "missing_skills": ["HubSpot"],
                    "description": "Create content strategy and campaign analytics dashboards.",
                    "link": "https://example.test/marketing",
                }
            ],
        )

        self.assertIn("82/100", body)
        self.assertIn("Matched skills: Campaign Analytics, Content Strategy", body)
        self.assertIn("Missing/weak signals: HubSpot", body)
        self.assertIn("Description: Create content strategy", body)

    def test_free_plain_digest_limits_premium_context(self) -> None:
        body = _plain_digest(
            {"name": "Person", "plan": "free"},
            [
                {
                    "title": "Marketing Coordinator",
                    "company": "Example",
                    "score": 82,
                    "score_source": "local",
                    "role_query": "Marketing & Communications",
                    "location": "San Francisco, CA",
                    "employment_type": "Full-time",
                    "workplace_type": "Hybrid",
                    "salary": "$70,000",
                    "posting_date": "2026-09-08",
                    "source": "LinkedIn",
                    "rationale": "Strong campaign analytics overlap.",
                    "matched_skills": ["Campaign Analytics", "Content Strategy"],
                    "missing_skills": ["HubSpot"],
                    "description": "Create content strategy and campaign analytics dashboards.",
                    "link": "https://example.test/marketing",
                }
            ],
        )

        self.assertIn("Free plan: upgrade to Pro", body)
        self.assertIn("Matched skills: Campaign Analytics, Content Strategy", body)
        self.assertNotIn("Missing/weak signals", body)
        self.assertNotIn("Description: Create content strategy", body)


if __name__ == "__main__":
    unittest.main()
