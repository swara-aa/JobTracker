from __future__ import annotations

import unittest

from job_agent.classification import (
    expand_role_preferences,
    infer_role_family,
    location_filter_options,
    location_matches,
    normalize_location_group,
)


class ClassificationTests(unittest.TestCase):
    def test_infers_broad_role_families_from_titles(self) -> None:
        examples = {
            "Junior Accountant": "Finance & Accounting",
            "Customer Success Manager": "Sales & Customer Success",
            "UX Researcher": "Design & UX",
            "Registered Nurse": "Healthcare",
            "Marketing Coordinator": "Marketing & Communications",
            "Data Analyst": "Data & Analytics",
        }

        for title, expected in examples.items():
            with self.subTest(title=title):
                self.assertEqual(infer_role_family(title), expected)

    def test_uses_title_words_for_unknown_fields(self) -> None:
        self.assertEqual(infer_role_family("Library Assistant"), "Library Assistant")

    def test_groups_city_and_state_locations(self) -> None:
        self.assertEqual(normalize_location_group("San Francisco, CA"), "California")
        self.assertEqual(normalize_location_group("California, United States"), "California")
        self.assertEqual(normalize_location_group("Remote - United States"), "Remote")

    def test_location_matching_uses_groups(self) -> None:
        self.assertTrue(location_matches("San Francisco, CA", "California"))
        self.assertTrue(location_matches("California, United States", "San Francisco"))
        self.assertTrue(location_matches("Remote", "remote"))
        self.assertFalse(location_matches("Austin, TX", "California"))

    def test_filter_options_include_normalized_groups(self) -> None:
        options = location_filter_options(["San Francisco, CA", "Austin, TX"])

        self.assertIn("California", options)
        self.assertIn("Texas", options)

    def test_major_preferences_expand_to_career_families(self) -> None:
        self.assertIn("Software Engineering", expand_role_preferences(["Computer Science"]))
        self.assertIn("Data & Analytics", expand_role_preferences(["Statistics"]))
        self.assertIn("Healthcare", expand_role_preferences(["Biology"]))
        self.assertIn("Finance & Accounting", expand_role_preferences(["Accounting"]))


if __name__ == "__main__":
    unittest.main()
