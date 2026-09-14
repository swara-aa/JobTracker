from __future__ import annotations

from datetime import datetime
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from job_agent.automation import (
    GEMINI_SUBMISSION_STATE_VERSION,
    _digest_scoring_wait_reason,
    _maybe_collect_public_boards,
    _maybe_submit_gemini_batch,
    _gemini_submission_failure_message,
    _reset_daily_batch_counter,
    request_public_collection_now,
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

    def test_public_collection_can_be_forced_after_daily_run(self) -> None:
        written: list[dict[str, object]] = []
        with (
            patch(
                "job_agent.automation._read_state",
                return_value={"last_public_collection_date": "2026-09-13"},
            ),
            patch("job_agent.automation._write_state", side_effect=written.append),
            patch("job_agent.automation.automation_status", return_value={"running": True}),
        ):
            status = request_public_collection_now()

        self.assertEqual(status, {"running": True})
        self.assertTrue(written[0]["force_public_collection_pending"])

    def test_forced_public_collection_bypasses_same_day_guard(self) -> None:
        states = [
            {
                "last_public_collection_date": "2026-09-13",
                "force_public_collection_pending": True,
            },
            {
                "last_public_collection_date": "2026-09-13",
                "force_public_collection_pending": False,
            },
        ]
        written: list[dict[str, object]] = []
        with (
            patch("job_agent.automation._read_state", side_effect=states),
            patch("job_agent.automation._write_state", side_effect=written.append),
            patch("job_agent.automation._now", return_value=datetime(2026, 9, 13, 7, 0)),
            patch("job_agent.collector.run_collection_and_prepare_matches", return_value={"saved_job_ids": []}),
        ):
            _maybe_collect_public_boards()

        self.assertFalse(written[0]["force_public_collection_pending"])
        self.assertEqual(written[-1]["public_jobs_saved"], 0)

    def test_noop_gemini_batch_does_not_consume_daily_submission(self) -> None:
        ready_at = "2026-09-13T09:00:00-05:00"
        states = [
            {"gemini_not_before": ready_at, "batch_submissions": 2, "gemini_submission_failures": 0},
            {"gemini_not_before": ready_at, "batch_submissions": 2, "gemini_submission_failures": 0},
            {"gemini_not_before": ready_at, "batch_submissions": 2, "gemini_submission_failures": 0},
        ]
        written: list[dict[str, object]] = []
        with (
            patch("job_agent.automation._read_state", side_effect=states),
            patch("job_agent.automation._write_state", side_effect=written.append),
            patch("job_agent.automation._now", return_value=datetime(2026, 9, 13, 9, 30, tzinfo=ZoneInfo("America/Chicago"))),
            patch("job_agent.automation.get_user_setting", return_value="fake-key"),
            patch("job_agent.gemini_batch.batch_status", return_value={"active": False}),
            patch("job_agent.storage.described_job_ids_without_gemini_match", return_value=[101]),
            patch("job_agent.storage.fetch_resumes", return_value=[{"id": 1, "content": "resume"}]),
            patch("job_agent.local_scoring.score_jobs_locally", return_value={"scored": 1, "resumes": 1}),
            patch("job_agent.visa_analysis.reassess_explicit_posting_language"),
            patch(
                "job_agent.gemini_batch.submit_gemini_resume_batch",
                return_value={"active": False, "message": "No described jobs are waiting."},
            ),
        ):
            _maybe_submit_gemini_batch()

        self.assertEqual(written[-1]["batch_submissions"], 2)
        self.assertEqual(written[-1]["gemini_submission_failures"], 0)


if __name__ == "__main__":
    unittest.main()
