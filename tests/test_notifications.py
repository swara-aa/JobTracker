from __future__ import annotations

import unittest
from unittest.mock import patch

from job_agent.notifications import send_operational_alert


class NotificationTests(unittest.TestCase):
    def test_operational_alert_uses_configured_recipient(self) -> None:
        with (
            patch("job_agent.notifications.config.SMTP_HOST", "smtp.example.com"),
            patch("job_agent.notifications.config.SMTP_PORT", 587),
            patch("job_agent.notifications.config.SMTP_USERNAME", "sender@example.com"),
            patch("job_agent.notifications.config.SMTP_PASSWORD", "secret"),
            patch("job_agent.notifications.config.SMTP_TO", ""),
            patch("job_agent.notifications.config.ALERT_EMAIL", "ops@example.com"),
            patch("job_agent.notifications.smtplib.SMTP") as smtp,
        ):
            sent = send_operational_alert("Gemini failed", "details")

        self.assertTrue(sent)
        message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        self.assertEqual(message["To"], "ops@example.com")
        self.assertEqual(message["From"], "sender@example.com")
        self.assertIn("JobTracker alert: Gemini failed", message["Subject"])

    def test_operational_alert_skips_when_unconfigured(self) -> None:
        with (
            patch("job_agent.notifications.config.SMTP_HOST", ""),
            patch("job_agent.notifications.config.ALERT_EMAIL", ""),
            patch("job_agent.notifications.config.SMTP_TO", ""),
        ):
            sent = send_operational_alert("Digest failed", "details")

        self.assertFalse(sent)


if __name__ == "__main__":
    unittest.main()
