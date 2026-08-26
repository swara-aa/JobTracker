from __future__ import annotations

import unittest

from job_agent.postgres_schema import schema_statements


class PostgresSchemaTests(unittest.TestCase):
    def test_schema_contains_all_persisted_tables(self) -> None:
        schema = "\n".join(schema_statements())
        for table in ("jobs", "resumes", "resume_job_matches", "application_helpers"):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", schema)

    def test_jobs_preserves_source_link_deduplication(self) -> None:
        schema = "\n".join(schema_statements())
        self.assertIn("UNIQUE(source, link)", schema)
