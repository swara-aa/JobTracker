from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from job_agent import config
from job_agent.database import translate_sqlite_placeholders


class DatabaseConfigTests(unittest.TestCase):
    def test_defaults_to_sqlite(self) -> None:
        with patch.object(config, "DATABASE_URL", ""):
            self.assertEqual(config.database_backend(), "sqlite")

    def test_recognizes_postgresql_url(self) -> None:
        with patch.object(config, "DATABASE_URL", "postgresql://user:password@example.test/jobs"):
            self.assertEqual(config.database_backend(), "postgresql")

    def test_rejects_unrecognized_database_url(self) -> None:
        with patch.object(config, "DATABASE_URL", "mysql://example.test/jobs"):
            with self.assertRaises(ValueError):
                config.database_backend()

    def test_postgres_translation_escapes_literal_percent_wildcards(self) -> None:
        query = "SELECT * FROM jobs WHERE (? = '' OR lower(company) LIKE '%' || lower(?) || '%')"

        translated = translate_sqlite_placeholders(query)

        self.assertEqual(
            translated,
            "SELECT * FROM jobs WHERE (%s = '' OR lower(company) LIKE '%%' || lower(%s) || '%%')",
        )
