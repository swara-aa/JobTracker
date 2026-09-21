from __future__ import annotations

from datetime import datetime, timezone
import json
import unittest
from unittest.mock import Mock, patch

import requests

from job_agent.apply_readiness import validate_apply_readiness


NOW = datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc)
JOB = {
    "title": "Software Engineer",
    "company": "Example",
    "link": "https://careers.example.com/jobs/123",
}


def response(
    body: str = "",
    *,
    status: int = 200,
    url: str = "https://careers.example.com/jobs/123",
    payload: dict[str, object] | None = None,
) -> Mock:
    result = Mock(spec=requests.Response)
    result.status_code = status
    result.url = url
    result.text = body
    if payload is None:
        result.json.side_effect = requests.JSONDecodeError("invalid", "", 0)
    else:
        result.json.return_value = payload
    return result


class ApplyReadinessTests(unittest.TestCase):
    def test_accepts_matching_page_with_live_apply_control(self) -> None:
        page = """
        <html><body>
          <h1>Software Engineer</h1><p>Example</p>
          <a href="/apply">Apply</a>
        </body></html>
        """
        with patch("job_agent.apply_readiness.requests.get", return_value=response(page)):
            result = validate_apply_readiness(JOB, now=NOW)

        self.assertTrue(result.is_open)
        self.assertEqual(result.reason, "Live application control found")

    def test_rejects_page_that_says_applications_are_closed(self) -> None:
        page = """
        <html><body>
          <h1>Software Engineer</h1><p>Example</p>
          <p>We are no longer accepting applications.</p>
          <a href="/apply">Apply now</a>
        </body></html>
        """
        with patch("job_agent.apply_readiness.requests.get", return_value=response(page)):
            result = validate_apply_readiness(JOB, now=NOW)

        self.assertEqual(result.status, "closed")

    def test_rejects_expired_structured_job_posting(self) -> None:
        posting = {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "Software Engineer",
            "validThrough": "2026-09-20T23:59:59Z",
        }
        page = f'<script type="application/ld+json">{json.dumps(posting)}</script>'
        with patch("job_agent.apply_readiness.requests.get", return_value=response(page)):
            result = validate_apply_readiness(JOB, now=NOW)

        self.assertEqual(result.status, "closed")
        self.assertEqual(result.reason, "Application deadline has passed")

    def test_date_only_deadline_remains_open_through_that_day(self) -> None:
        posting = {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "Software Engineer",
            "validThrough": "2026-09-21",
        }
        page = f'<script type="application/ld+json">{json.dumps(posting)}</script>'
        with patch("job_agent.apply_readiness.requests.get", return_value=response(page)):
            result = validate_apply_readiness(JOB, now=NOW)

        self.assertTrue(result.is_open)

    def test_rejects_redirect_to_general_careers_page(self) -> None:
        page = "<h1>Example Careers</h1><a href='/jobs'>Apply now</a>"
        with patch(
            "job_agent.apply_readiness.requests.get",
            return_value=response(page, url="https://careers.example.com/careers"),
        ):
            result = validate_apply_readiness(JOB, now=NOW)

        self.assertEqual(result.status, "unverified")
        self.assertIn("general careers page", result.reason)

    def test_greenhouse_api_confirms_active_posting(self) -> None:
        job = JOB | {
            "link": "https://job-boards.greenhouse.io/example/jobs/456",
        }
        api_response = response(
            payload={"id": 456, "title": "Software Engineer"},
            url="https://boards-api.greenhouse.io/v1/boards/example/jobs/456",
        )
        with patch("job_agent.apply_readiness.requests.get", return_value=api_response) as get:
            result = validate_apply_readiness(job, now=NOW)

        self.assertTrue(result.is_open)
        self.assertIn("boards-api.greenhouse.io", get.call_args.args[0])

    def test_http_not_found_is_closed(self) -> None:
        with patch(
            "job_agent.apply_readiness.requests.get",
            return_value=response(status=404),
        ):
            result = validate_apply_readiness(JOB, now=NOW)

        self.assertEqual(result.status, "closed")


if __name__ == "__main__":
    unittest.main()
