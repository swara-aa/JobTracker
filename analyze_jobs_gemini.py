from __future__ import annotations

import argparse
import sqlite3
import time

from job_agent.config import DB_PATH
from job_agent.gemini_analysis import analyze_job
from job_agent.storage import ensure_database


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze public job URLs with Gemini.")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()

    ensure_database()
    statuses = [""]
    if args.retry_failed:
        statuses.append("failed")
    placeholders = ",".join("?" for _ in statuses)
    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute(
            f"""
            SELECT id, title, company
            FROM jobs
            WHERE gemini_status IN ({placeholders})
            ORDER BY posting_date DESC
            LIMIT ?
            """,
            (*statuses, args.limit),
        ).fetchall()

    for index, (job_id, title, company) in enumerate(rows, 1):
        print(f"[{index}/{len(rows)}] {title} at {company}")
        try:
            analyze_job(job_id)
            print("  complete")
        except Exception as exc:  # noqa: BLE001
            print(f"  failed: {exc}")
        if index < len(rows):
            time.sleep(args.delay)


if __name__ == "__main__":
    main()
