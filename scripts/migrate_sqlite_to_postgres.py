from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from job_agent.postgres_schema import schema_statements

TABLES = ("jobs", "resumes", "resume_job_matches", "application_helpers")


def _copy_table(source: sqlite3.Connection, target, table: str) -> int:
    rows = source.execute(f'SELECT * FROM "{table}"').fetchall()
    if not rows:
        return 0
    columns = [column[1] for column in source.execute(f'PRAGMA table_info("{table}")')]
    names = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join("%s" for _ in columns)
    statement = f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})'
    with target.cursor() as cursor:
        cursor.executemany(statement, rows)
    return len(rows)


def migrate(source_path: Path, database_url: str) -> dict[str, int]:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("Install requirements.txt before running this migration.") from exc
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {source_path}")
    source = sqlite3.connect(source_path)
    counts: dict[str, int] = {}
    try:
        with psycopg.connect(database_url) as target:
            with target.cursor() as cursor:
                for statement in schema_statements():
                    cursor.execute(statement)
                for table in reversed(TABLES):
                    cursor.execute(f'TRUNCATE TABLE "{table}" CASCADE')
            for table in TABLES:
                counts[table] = _copy_table(source, target, table)
            with target.cursor() as cursor:
                for table in ("jobs", "resumes"):
                    cursor.execute(
                        "SELECT setval(pg_get_serial_sequence(%s, 'id'), "
                        "COALESCE((SELECT MAX(id) FROM " + table + "), 1), true)",
                        (table,),
                    )
        return counts
    finally:
        source.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy JobTracker SQLite data into PostgreSQL.")
    parser.add_argument("--source", default="data/jobs.db", help="Path to the local SQLite database.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("Provide --database-url or set DATABASE_URL.")
    counts = migrate(Path(args.source), args.database_url)
    print("Migration complete: " + ", ".join(f"{table}={count}" for table, count in counts.items()))


if __name__ == "__main__":
    main()
