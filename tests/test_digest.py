from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from job_agent.digest import (
    _digest_score,
    _plain_digest,
    active_digest_subscribers,
    send_digest_to_subscriber,
    subscriber_by_token,
    subscribe_to_digest,
    top_digest_matches,
    update_digest_preferences,
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
                patch("job_agent.digest.config.DIGEST_REQUIRE_GEMINI", False),
                patch("job_agent.digest.config.DIGEST_MIN_SCORE", 0),
                patch(
                    "job_agent.digest._verified_apply_ready_matches",
                    side_effect=lambda matches, **_kwargs: matches,
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
        self.assertIn("Priority signals: None detected", body)
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

    def test_no_match_digest_sends_instead_of_silently_skipping(self) -> None:
        subscriber = {
            "id": 1,
            "email": "person@example.com",
            "name": "Person",
            "plan": "free",
        }
        with (
            patch("job_agent.digest.top_digest_matches", return_value=[]),
            patch("job_agent.digest.config.SMTP_HOST", "smtp.example.com"),
            patch("job_agent.digest.config.SMTP_PORT", 587),
            patch("job_agent.digest.config.SMTP_USERNAME", "sender@example.com"),
            patch("job_agent.digest.config.SMTP_PASSWORD", "secret"),
            patch("job_agent.digest.smtplib.SMTP") as smtp,
            patch("job_agent.digest._record_deliveries") as record_deliveries,
        ):
            result = send_digest_to_subscriber(subscriber, use_gemini=False)

        self.assertTrue(result["sent"])
        message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        self.assertEqual(message["Subject"], "No verified-open 70+ job matches today")
        record_deliveries.assert_called_once_with(1, [])

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

    def test_digest_excludes_matches_below_fallback_score(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "jobs.db"
            with (
                patch("job_agent.storage.DB_PATH", database_path),
                patch("job_agent.digest.DB_PATH", database_path),
                patch("job_agent.database.DB_PATH", database_path),
                patch("job_agent.digest.get_user_setting", return_value=""),
                patch("job_agent.digest.config.DIGEST_REQUIRE_GEMINI", False),
                patch("job_agent.digest.config.DIGEST_MIN_SCORE", 90),
                patch(
                    "job_agent.digest._verified_apply_ready_matches",
                    side_effect=lambda matches, **_kwargs: matches,
                ),
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

    def test_digest_falls_back_to_80_then_70_with_score_notes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "jobs.db"
            scores_by_title = {
                "Best Available": 84,
                "Still Useful": 73,
                "Too Weak": 64,
            }
            with (
                patch("job_agent.storage.DB_PATH", database_path),
                patch("job_agent.digest.DB_PATH", database_path),
                patch("job_agent.database.DB_PATH", database_path),
                patch("job_agent.digest.get_user_setting", return_value=""),
                patch("job_agent.digest.config.DIGEST_REQUIRE_GEMINI", False),
                patch("job_agent.digest.config.DIGEST_MIN_SCORE", 90),
                patch(
                    "job_agent.digest._verified_apply_ready_matches",
                    side_effect=lambda matches, **_kwargs: matches,
                ),
                patch(
                    "job_agent.digest._digest_score",
                    side_effect=lambda job, _resume: (
                        {
                            "score": scores_by_title[str(job["title"])],
                            "rationale": "Best available verified-open match.",
                            "evidence": [],
                            "missing": [],
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
                            title=title,
                            company="Example",
                            location="San Francisco, CA",
                            posting_date=datetime.now(timezone.utc),
                            link=f"https://example.test/{title.replace(' ', '-').lower()}",
                            description="Create content strategy and campaign analytics.",
                        )
                        for title in scores_by_title
                    ]
                )
                subscriber_record = active_digest_subscribers()[0] | subscriber
                matches = top_digest_matches(subscriber_record)
                body = _plain_digest(subscriber_record, matches)

        self.assertEqual([match["title"] for match in matches], ["Best Available", "Still Useful"])
        self.assertEqual(matches[0]["score_floor"], 80)
        self.assertEqual(matches[1]["score_floor"], 70)
        self.assertIn("not enough verified-open 90+", body.lower())
        self.assertIn("fallback tier", body)

    def test_subscriber_preferences_can_be_managed_by_token(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "jobs.db"
            with (
                patch("job_agent.storage.DB_PATH", database_path),
                patch("job_agent.digest.DB_PATH", database_path),
                patch("job_agent.database.DB_PATH", database_path),
            ):
                subscribe_to_digest(
                    email="person@example.com",
                    name="Person",
                    roles=["Marketing & Communications"],
                    location="California",
                    resume_filename="resume.txt",
                    resume_content=RESUME_TEXT,
                )
                subscriber = active_digest_subscribers()[0]
                token = str(subscriber["unsubscribe_token"])
                updated = update_digest_preferences(
                    token,
                    name="Updated",
                    plan="pro",
                    roles=["Finance & Accounting"],
                    location="Remote",
                )
                refreshed = subscriber_by_token(token)

        self.assertTrue(updated)
        self.assertIsNotNone(refreshed)
        self.assertEqual(refreshed["name"], "Updated")
        self.assertEqual(refreshed["plan"], "pro")
        self.assertEqual(refreshed["roles"], ["Finance & Accounting"])
        self.assertEqual(refreshed["location"], "Remote")

    def test_pro_digest_uses_gemini_scores_before_thresholding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "jobs.db"
            with (
                patch("job_agent.storage.DB_PATH", database_path),
                patch("job_agent.digest.DB_PATH", database_path),
                patch("job_agent.database.DB_PATH", database_path),
                patch("job_agent.digest.get_user_setting", return_value="fake-key"),
                patch("job_agent.digest.config.DIGEST_REQUIRE_GEMINI", True),
                patch("job_agent.digest.config.DIGEST_MIN_SCORE", 90),
                patch(
                    "job_agent.digest._verified_apply_ready_matches",
                    side_effect=lambda matches, **_kwargs: matches,
                ),
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

    def test_digest_ranks_qualified_jobs_by_company_priority_signals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "jobs.db"
            now = datetime.now(timezone.utc)
            with (
                patch("job_agent.storage.DB_PATH", database_path),
                patch("job_agent.digest.DB_PATH", database_path),
                patch("job_agent.database.DB_PATH", database_path),
                patch("job_agent.digest.get_user_setting", return_value=""),
                patch("job_agent.digest.config.DIGEST_REQUIRE_GEMINI", True),
                patch("job_agent.digest.config.DIGEST_MIN_SCORE", 90),
                patch(
                    "job_agent.digest._verified_apply_ready_matches",
                    side_effect=lambda matches, **_kwargs: matches,
                ),
                patch(
                    "job_agent.digest.get_company_attributes",
                    side_effect=lambda company_name: {
                        "fortune_500": company_name == "Microsoft",
                        "sponsors_h1b": company_name == "Microsoft",
                        "hires_software_engineers": company_name == "Microsoft",
                    },
                ),
                patch(
                    "job_agent.digest._digest_score",
                    return_value=(
                        {
                            "score": 92,
                            "rationale": "Strong match.",
                            "evidence": ["Python"],
                            "missing": [],
                            "hard_no": False,
                        },
                        "Gemini",
                    ),
                ),
            ):
                subscriber = subscribe_to_digest(
                    email="person@example.com",
                    name="Person",
                    roles=["Software Engineering"],
                    location="California",
                    resume_filename="resume.txt",
                    resume_content=RESUME_TEXT,
                )
                save_jobs(
                    [
                        JobPosting(
                            source="test",
                            role_query="Software Engineering",
                            title="Software Engineer",
                            company="StartupCo",
                            location="San Francisco, CA",
                            posting_date=now,
                            link="https://example.test/startup",
                            description="Build Python services for user-facing applications.",
                        ),
                        JobPosting(
                            source="test",
                            role_query="Software Engineering",
                            title="Software Engineer",
                            company="Microsoft",
                            location="San Francisco, CA",
                            posting_date=now - timedelta(days=2),
                            link="https://example.test/microsoft",
                            description="Build Python services for cloud products with reliable APIs.",
                        ),
                    ]
                )
                subscriber_record = active_digest_subscribers()[0] | subscriber
                matches = top_digest_matches(subscriber_record)

        self.assertEqual([match["company"] for match in matches], ["Microsoft", "StartupCo"])
        self.assertIn("Fortune 500", matches[0]["priority_signals"])
        self.assertIn("visa-friendly signal", matches[0]["priority_signals"])
        self.assertIn("software hiring signal", matches[0]["priority_signals"])

    def test_digest_can_require_gemini_scored_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "jobs.db"
            with (
                patch("job_agent.storage.DB_PATH", database_path),
                patch("job_agent.digest.DB_PATH", database_path),
                patch("job_agent.database.DB_PATH", database_path),
                patch("job_agent.digest.get_user_setting", return_value=""),
                patch("job_agent.digest.config.DIGEST_REQUIRE_GEMINI", True),
                patch("job_agent.digest.config.DIGEST_MIN_SCORE", 0),
                patch(
                    "job_agent.digest._verified_apply_ready_matches",
                    side_effect=lambda matches, **_kwargs: matches,
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
                            link="https://example.test/local-only",
                            description="Create content strategy, campaign analytics, and social media reporting.",
                        ),
                    ]
                )
                subscriber_record = active_digest_subscribers()[0] | subscriber
                matches = top_digest_matches(subscriber_record)

        self.assertEqual(matches, [])


if __name__ == "__main__":
    unittest.main()
