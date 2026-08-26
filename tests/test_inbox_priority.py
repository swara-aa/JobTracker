from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from job_agent.web import (
    INBOX_FORTUNE_500_BONUS,
    INBOX_RECENT_FRESHNESS_BONUS,
    INBOX_URGENT_FRESHNESS_BONUS,
    _priority_sort_key,
    _priority_urgency_bonus,
    _build_priority_queue,
)


class InboxPriorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)

    def _job(self, score: int, age: timedelta) -> dict[str, object]:
        return {
            "posting_date": (self.now - age).isoformat(),
            "resume_match_score": score,
            "local_match_score": None,
            "application_status": "Saved",
        }

    def test_urgency_bonuses_follow_the_inbox_windows(self) -> None:
        self.assertEqual(
            _priority_urgency_bonus(self._job(80, timedelta(hours=12)), now=self.now),
            INBOX_URGENT_FRESHNESS_BONUS,
        )
        self.assertEqual(
            _priority_urgency_bonus(self._job(80, timedelta(days=2)), now=self.now),
            INBOX_RECENT_FRESHNESS_BONUS,
        )
        self.assertEqual(_priority_urgency_bonus(self._job(80, timedelta(days=4)), now=self.now), 0)

    def test_fresh_86_match_outranks_a_four_day_old_90_match(self) -> None:
        fresh_job = self._job(86, timedelta(hours=4))
        older_job = self._job(90, timedelta(days=4))
        self.assertGreater(
            _priority_sort_key(fresh_job, now=self.now),
            _priority_sort_key(older_job, now=self.now),
        )

    def test_fortune_500_job_gets_a_modest_priority_bonus(self) -> None:
        fortune_job = self._job(85, timedelta(days=2))
        fortune_job["company"] = "Example Fortune Company"
        other_job = self._job(87, timedelta(days=2))
        other_job["company"] = "Other Company"
        with patch(
            "job_agent.web.get_company_attributes",
            side_effect=lambda company_name: {"fortune_500": company_name == "Example Fortune Company"},
        ):
            self.assertGreater(
                _priority_sort_key(fortune_job, now=self.now),
                _priority_sort_key(other_job, now=self.now),
            )
        self.assertEqual(INBOX_FORTUNE_500_BONUS, 3)

    def test_company_title_mismatch_is_not_a_must_apply_now_job(self) -> None:
        job = self._job(90, timedelta(hours=2))
        job.update({
            "id": 1,
            "title": "Software Engineer (Verified job)",
            "company": "Software Engineer",
            "location": "Remote",
            "resume_match_hard_no": False,
            "local_match_hard_no": False,
        })
        job["posting_date"] = datetime.now(timezone.utc).isoformat()
        self.assertEqual(_build_priority_queue([job]), [])


if __name__ == "__main__":
    unittest.main()
