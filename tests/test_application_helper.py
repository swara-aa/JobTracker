from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from job_agent.application_helper import _parse_helper_response, generate_application_helper


class ApplicationHelperTests(unittest.TestCase):
    def test_parses_complete_helper(self) -> None:
        helper = _parse_helper_response(
            json.dumps(
                {
                    "resume_emphasis": ["Built a Python API project."],
                    "keywords_to_weave": ["Python"],
                    "gaps_to_handle": ["No material gap identified"],
                    "tailoring_steps": ["Lead with the API project."],
                    "cover_letter": "Dear Hiring Team,\n\nI am interested in this role.",
                }
            )
        )
        self.assertEqual(helper["keywords_to_weave"], ["Python"])
        self.assertIn("Dear Hiring Team", helper["cover_letter"])

    def test_rejects_incomplete_helper(self) -> None:
        with self.assertRaises(ValueError):
            _parse_helper_response(json.dumps({"cover_letter": "Only this field"}))

    def test_generation_requires_an_explicitly_configured_key(self) -> None:
        with patch("job_agent.application_helper.get_user_setting", return_value=""):
            with self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY"):
                generate_application_helper(1, 1)


if __name__ == "__main__":
    unittest.main()
