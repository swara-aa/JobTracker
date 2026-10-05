from __future__ import annotations

import json
import unittest

from job_agent.resume_matcher import _parse_rankings_response


class ResumeMatcherTests(unittest.TestCase):
    def test_parse_rankings_response_accepts_valid_payload(self) -> None:
        rankings = _parse_rankings_response(
            json.dumps(
                {
                    "rankings": [
                        {
                            "resume_id": 1,
                            "score": 88,
                            "rationale": "Strong Python evidence.",
                            "matched_skills": ["Python"],
                            "missing_skills": ["Kubernetes"],
                            "improvements": ["Add a deployment project."],
                            "hard_no": False,
                            "hard_no_reasons": [],
                        }
                    ]
                }
            ),
            {1},
        )

        self.assertEqual(rankings[0]["score"], 88)

    def test_parse_rankings_response_wraps_malformed_json(self) -> None:
        with self.assertRaisesRegex(ValueError, "malformed ranking JSON"):
            _parse_rankings_response('{"rankings": [{"resume_id": 1, "rationale": "unterminated}', {1})

    def test_parse_rankings_response_validates_expected_resume_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one result"):
            _parse_rankings_response('{"rankings": []}', {1})


if __name__ == "__main__":
    unittest.main()
