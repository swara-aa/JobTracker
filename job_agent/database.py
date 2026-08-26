from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import sqlite3
from typing import Any

from job_agent.config import DATABASE_URL, DB_PATH, database_backend


def backend_name() -> str:
    return database_backend()


@contextmanager
def sqlite_connection() -> Iterator[sqlite3.Connection]:
    """Open the existing local database explicitly for SQLite-only operations."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    try:
        yield connection
    finally:
        connection.close()


def postgres_connection() -> Any:
    """Open a PostgreSQL connection only when PostgreSQL is explicitly configured."""
    if backend_name() != "postgresql":
        raise RuntimeError("PostgreSQL is not configured.")
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("PostgreSQL support requires psycopg. Install requirements.txt.") from exc
    return psycopg.connect(DATABASE_URL)


class PostgresRow(dict[str, Any]):
    """Dictionary row with the index access expected by existing SQLite callers."""

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

    def __iter__(self):
        return iter(self.values())


class PostgresCursor:
    def __init__(self, cursor: Any):
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def fetchone(self) -> PostgresRow | None:
        row = self._cursor.fetchone()
        return PostgresRow(row) if row is not None else None

    def fetchall(self) -> list[PostgresRow]:
        return [PostgresRow(row) for row in self._cursor.fetchall()]


class PostgresConnection:
    """Small compatibility layer for the existing parameterized storage queries."""

    def __init__(self) -> None:
        from psycopg.rows import dict_row

        self._connection = postgres_connection()
        self._connection.row_factory = dict_row
        self.row_factory: Any = None

    def __enter__(self) -> "PostgresConnection":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if exc_type is None:
            self.commit()
        else:
            self._connection.rollback()
        self._connection.close()

    def execute(self, query: str, parameters: Any = ()) -> PostgresCursor:
        translated = query.replace("?", "%s")
        cursor = self._connection.cursor()
        cursor.execute(translated, parameters)
        return PostgresCursor(cursor)

    def commit(self) -> None:
        self._connection.commit()


def connect() -> sqlite3.Connection | PostgresConnection:
    if backend_name() == "sqlite":
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(DB_PATH)
    return PostgresConnection()
