from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_agent.company_intelligence import CompanyIntelligence
from job_agent.local_scoring import _score_resume


HEADERS = [
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


def _write_companies(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)


class CompanyIntelligenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.csv_path = Path(self.temporary_directory.name) / "companies.csv"
        _write_companies(
            self.csv_path,
            [
                {
                    "company_name": "Google",
                    "aliases": "Google LLC|Alphabet",
                    "fortune_500": "true",
                    "visa_friendly": "true",
                    "sponsors_h1b": "true",
                    "hires_entry_level": "true",
                    "hires_software_engineers": "true",
                    "hires_ai_ml": "true",
                    "industry": "Technology",
                    "careers_url": "https://careers.google.com",
                    "notes": "test data",
                }
            ],
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_exact_company_name_matching(self) -> None:
        company = CompanyIntelligence(self.csv_path).get_company("Google")
        self.assertEqual(company["company_name"], "Google")
        self.assertEqual(company["matched_by"], "primary")

    def test_alias_matching(self) -> None:
        company = CompanyIntelligence(self.csv_path).get_company("Alphabet")
        self.assertEqual(company["company_name"], "Google")
        self.assertEqual(company["matched_by"], "alias")

    def test_matching_is_case_insensitive_and_normalizes_suffixes(self) -> None:
        company = CompanyIntelligence(self.csv_path).get_company("google, inc.")
        self.assertEqual(company["company_name"], "Google")
        self.assertTrue(CompanyIntelligence(self.csv_path).is_known_company("GOOGLE LLC"))

    def test_unknown_company(self) -> None:
        intelligence = CompanyIntelligence(self.csv_path)
        self.assertIsNone(intelligence.get_company("Independent Startup"))
        self.assertFalse(intelligence.is_known_company("Independent Startup"))
        self.assertEqual(
            intelligence.get_company_attributes("Independent Startup")["matched_by"], "unknown"
        )

    def test_duplicate_company_rows_keep_the_first_record(self) -> None:
        _write_companies(
            self.csv_path,
            [
                {
                    "company_name": "Google",
                    "aliases": "",
                    "fortune_500": "true",
                    "visa_friendly": "true",
                    "sponsors_h1b": "",
                    "hires_entry_level": "",
                    "hires_software_engineers": "",
                    "hires_ai_ml": "",
                    "industry": "Technology",
                    "careers_url": "",
                    "notes": "first",
                },
                {
                    "company_name": "Google LLC",
                    "aliases": "",
                    "fortune_500": "false",
                    "visa_friendly": "false",
                    "sponsors_h1b": "",
                    "hires_entry_level": "",
                    "hires_software_engineers": "",
                    "hires_ai_ml": "",
                    "industry": "",
                    "careers_url": "",
                    "notes": "second",
                },
            ],
        )
        with self.assertLogs("job_agent.company_intelligence", level="WARNING"):
            company = CompanyIntelligence(self.csv_path).get_company("Google")
        self.assertTrue(company["visa_friendly"])
        self.assertEqual(company["notes"], "first")

    def test_malformed_rows_are_logged_without_stopping_valid_rows(self) -> None:
        _write_companies(
            self.csv_path,
            [
                {header: "" for header in HEADERS} | {"aliases": "Broken Alias"},
                {
                    "company_name": "Google",
                    "aliases": "",
                    "fortune_500": "possibly",
                    "visa_friendly": "",
                    "sponsors_h1b": "",
                    "hires_entry_level": "",
                    "hires_software_engineers": "",
                    "hires_ai_ml": "",
                    "industry": "Technology",
                    "careers_url": "",
                    "notes": "valid company with one invalid value",
                },
            ],
        )
        with self.assertLogs("job_agent.company_intelligence", level="WARNING"):
            company = CompanyIntelligence(self.csv_path).get_company("Google")
        self.assertIsNotNone(company)
        self.assertIsNone(company["fortune_500"])

    def test_missing_csv_file_is_safe(self) -> None:
        missing_path = Path(self.temporary_directory.name) / "missing.csv"
        with self.assertLogs("job_agent.company_intelligence", level="WARNING"):
            intelligence = CompanyIntelligence(missing_path)
        self.assertIsNone(intelligence.get_company("Google"))

    def test_company_metadata_modestly_improves_local_ranking(self) -> None:
        job = {
            "id": 1,
            "company": "Google",
            "title": "Entry-Level Machine Learning Software Engineer",
            "description": "Required: Python, JavaScript, Docker, Machine Learning, PyTorch, and SQL.",
        }
        resume = {
            "id": 1,
            "content": "Bachelor of Science in Computer Science. Python JavaScript Docker Machine Learning PyTorch SQL.",
        }
        company = {
            "fortune_500": True,
            "visa_friendly": True,
            "sponsors_h1b": True,
            "hires_entry_level": True,
            "hires_software_engineers": True,
            "hires_ai_ml": True,
        }
        with patch("job_agent.local_scoring.get_company", return_value=None):
            unknown_score = _score_resume(job, resume)["score"]
        with patch("job_agent.local_scoring.get_company", return_value=company):
            enriched_score = _score_resume(job, resume)["score"]
        self.assertGreater(enriched_score, unknown_score)
        self.assertLessEqual(enriched_score - unknown_score, 9)


if __name__ == "__main__":
    unittest.main()
