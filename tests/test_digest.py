from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from job_agent.digest import (
    _digest_score,
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
                patch("job_agent.digest.config.DIGEST_MIN_SCORE", 0),
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

    def test_digest_score_prefers_stored_gemini_match(self) -> None:
        score, source = _digest_score(
            {
                "resume_match_score": 91,
                "resume_match_rationale": "Strong match on analytics and campaign execution.",
                "resume_match_matched_skills": '["Campaign Analytics", "Content Strategy"]',
                "resume_match_missing_skills": '["HubSpot"]',
                "resume_match_hard_no": 0,
            },
            {"content": RESUME_TEXT},
        )

        self.assertEqual(source, "Gemini")
        self.assertEqual(score["score"], 91)
        self.assertEqual(score["evidence"], ["Campaign Analytics", "Content Strategy"])
        self.assertEqual(score["missing"], ["HubSpot"])

    def test_digest_excludes_matches_below_minimum_score(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "jobs.db"
            with (
                patch("job_agent.storage.DB_PATH", database_path),
                patch("job_agent.digest.DB_PATH", database_path),
                patch("job_agent.database.DB_PATH", database_path),
                patch("job_agent.digest.get_user_setting", return_value=""),
                patch("job_agent.digest.config.DIGEST_MIN_SCORE", 90),
                patch(
                    "job_agent.digest._digest_score",
                    return_value=(
                        {
                            "score": 25,
                            "rationale": "Weak match.",
                            "evidence": [],
                            "missing": ["Python"],
                            "hard_no": False,
                        },
                        "local",
                    ),
                ),
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
                            description="Create content strategy and campaign analytics.",
                        ),
                    ]
                )
                subscriber_record = active_digest_subscribers()[0] | subscriber
                matches = top_digest_matches(subscriber_record)

        self.assertEqual(matches, [])

    def test_pro_digest_uses_gemini_scores_before_thresholding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "jobs.db"
            with (
                patch("job_agent.storage.DB_PATH", database_path),
                patch("job_agent.digest.DB_PATH", database_path),
                patch("job_agent.database.DB_PATH", database_path),
                patch("job_agent.digest.get_user_setting", return_value="fake-key"),
                patch("job_agent.digest.config.DIGEST_MIN_SCORE", 90),
                patch(
                    "job_agent.digest._score_digest_with_gemini",
                    side_effect=lambda _resume, matches: [
                        match | {"score": 95 if match["title"] == "Great Match" else 25, "score_source": "Gemini"}
                        for match in matches
                    ],
                ),
            ):
                subscriber = subscribe_to_digest(
                    email="person@example.com",
                    name="Person",
                    roles=["Marketing & Communications"],
                    location="California",
                    resume_filename="resume.txt",
                    resume_content=RESUME_TEXT,
                    plan="pro",
                )
                save_jobs(
                    [
                        JobPosting(
                            source="test",
                            role_query="Marketing & Communications",
                            title="Weak Match",
                            company="Example",
                            location="San Francisco, CA",
                            posting_date=datetime.now(timezone.utc),
                            link="https://example.test/weak",
                            description="General marketing support.",
                        ),
                        JobPosting(
                            source="test",
                            role_query="Marketing & Communications",
                            title="Great Match",
                            company="Example",
                            location="San Francisco, CA",
                            posting_date=datetime.now(timezone.utc),
                            link="https://example.test/great",
                            description="Campaign analytics, content strategy, and dashboards.",
                        ),
                    ]
                )
                subscriber_record = active_digest_subscribers()[0] | subscriber
                matches = top_digest_matches(subscriber_record)

        self.assertEqual([match["title"] for match in matches], ["Great Match"])
        self.assertEqual(matches[0]["score"], 95)


if __name__ == "__main__":
    unittest.main()
