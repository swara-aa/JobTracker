from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from job_agent import config


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
