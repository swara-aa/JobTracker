from __future__ import annotations

import unittest
from unittest.mock import patch

from job_agent.automation import (
    GEMINI_SUBMISSION_STATE_VERSION,
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


if __name__ == "__main__":
    unittest.main()
