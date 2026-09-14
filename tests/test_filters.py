from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from job_agent.filters import matches_role_query
from job_agent.sources import BaseSource


class FilterTests(unittest.TestCase):
    def test_role_family_matches_posting_description(self) -> None:
        self.assertTrue(
            matches_role_query(
                "Business Systems Associate",
                "Software Engineering",
                "Build backend services, APIs, and frontend workflows with Python.",
            )
        )

    def test_entry_level_filter_is_not_required_by_default(self) -> None:
        source = _ConcreteSource()
        with patch("job_agent.config.ENTRY_LEVEL_ONLY", False):
            self.assertTrue(
                source._keep(
                    "Software Engineering",
                    "Software Engineer",
                    "San Francisco, CA",
                    datetime.now(timezone.utc),
                    "Build backend services.",
                )
            )


class _ConcreteSource(BaseSource):
    name = "test"

    def fetch(self, role_query: str):
        return []


if __name__ == "__main__":
    unittest.main()
