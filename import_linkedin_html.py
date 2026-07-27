from __future__ import annotations

import argparse
from pathlib import Path

from job_agent.linkedin_html import parse_linkedin_html
from job_agent.storage import save_jobs


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a saved LinkedIn Jobs HTML page.")
    parser.add_argument("html_file", type=Path)
    args = parser.parse_args()

    jobs = parse_linkedin_html(args.html_file)
    saved = save_jobs(jobs)
    print(f"Parsed {len(jobs)} job(s); saved {saved} new job(s).")


if __name__ == "__main__":
    main()
