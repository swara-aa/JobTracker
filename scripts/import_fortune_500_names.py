"""Merge a public Fortune 500 name table into the local company database."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from job_agent.company_intelligence import REQUIRED_COLUMNS, normalize_company_name


DEFAULT_SOURCE_URL = "https://us500.com/fortune-500-companies"
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "config" / "companies.csv"
SOURCE_NOTE = "Fortune 500 2026 name imported from the public US500 table; other fields are unknown."


def fetch_source_html(source_url: str) -> str:
    response = requests.get(source_url, timeout=30)
    response.raise_for_status()
    return response.text


def extract_company_names(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    companies: list[str] = []
    for row in soup.select("tbody tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.select("td")]
        if len(cells) < 2 or not cells[0].isdigit():
            continue
        rank = int(cells[0])
        if 1 <= rank <= 500:
            companies.append(cells[1])
    if len(companies) != 500 or len(set(companies)) != 500:
        raise ValueError("Expected exactly 500 distinct ranked company names in the source table.")
    return companies


def _name_index(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for row in rows:
        for name in [row["company_name"], *row["aliases"].split("|")]:
            normalized = normalize_company_name(name)
            if normalized:
                index.setdefault(normalized, row)
    return index


def merge_company_names(csv_path: Path, company_names: list[str]) -> tuple[int, int]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if set(reader.fieldnames or []) != REQUIRED_COLUMNS:
            raise ValueError(f"Unexpected company CSV columns in {csv_path}.")
        rows = [dict(row) for row in reader if any((value or "").strip() for value in row.values())]

    index = _name_index(rows)
    updated = 0
    added = 0
    for company_name in company_names:
        existing = index.get(normalize_company_name(company_name))
        if existing is not None:
            if existing.get("fortune_500") != "true":
                existing["fortune_500"] = "true"
                updated += 1
            continue
        row = {column: "" for column in REQUIRED_COLUMNS}
        row.update(
            {
                "company_name": company_name,
                "fortune_500": "true",
                "notes": SOURCE_NOTE,
            }
        )
        rows.append(row)
        index[normalize_company_name(company_name)] = row
        added += 1

    ordered_columns = [
        "company_name",
        "aliases",
        "fortune_500",
        "visa_friendly",
        "sponsors_h1b",
        "hires_entry_level",
        "hires_software_engineers",
        "hires_ai_ml",
        "industry",
        "careers_url",
        "notes",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ordered_columns)
        writer.writeheader()
        writer.writerows(rows)
    return added, updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    parser.add_argument("--source-html", type=Path)
    arguments = parser.parse_args()

    html = (
        arguments.source_html.read_text(encoding="utf-8")
        if arguments.source_html
        else fetch_source_html(arguments.source_url)
    )
    added, updated = merge_company_names(arguments.database, extract_company_names(html))
    print(f"Added {added} companies and updated {updated} existing companies in {arguments.database}.")


if __name__ == "__main__":
    main()
