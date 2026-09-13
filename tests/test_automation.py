from __future__ import annotations

from datetime import datetime
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from job_agent.automation import (
    GEMINI_SUBMISSION_STATE_VERSION,
    _digest_scoring_wait_reason,
    _gemini_submission_failure_message,
    _reset_daily_batch_counter,
)


class GeminiAutomationTests(unittest.TestCase):
    def test_failed_precondition_includes_actionable_guidance(self) -> None:
        message = _gemini_submission_failure_message(
            RuntimeError("400 FAILED_PRECONDITION"),
            1,
        )
        self.assertIn("attempt 1/3", message)
        self.assertIn("billing", message)

    def test_legacy_state_is_migrated_before_submission(self) -> None:
        legacy_state = {"batch_date": "2026-08-10", "batch_submissions": 3}
        written: list[dict[str, object]] = []
        with (
            patch("job_agent.automation._read_stored_state", return_value=legacy_state),
            patch("job_agent.automation._write_state", side_effect=written.append),
        ):
            _reset_daily_batch_counter()
        self.assertEqual(written[0]["batch_submissions"], 0)
        self.assertEqual(
            written[0]["gemini_submission_state_version"],
            GEMINI_SUBMISSION_STATE_VERSION,
        )

    def test_digest_waits_for_active_gemini_before_latest_send_time(self) -> None:
        now = datetime(2026, 9, 13, 9, 15, tzinfo=ZoneInfo("America/Chicago"))
        with patch("job_agent.gemini_batch.batch_status", return_value={"active": True}):
            reason = _digest_scoring_wait_reason({}, now)

        self.assertEqual(reason, "Gemini scoring to finish")

    def test_digest_does_not_wait_after_latest_send_time(self) -> None:
        now = datetime(2026, 9, 13, 9, 30, tzinfo=ZoneInfo("America/Chicago"))
        with patch("job_agent.gemini_batch.batch_status", return_value={"active": True}):
            reason = _digest_scoring_wait_reason({}, now)

        self.assertEqual(reason, "")


if __name__ == "__main__":
    unittest.main()
