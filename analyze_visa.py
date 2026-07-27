from pathlib import Path

from job_agent.visa_analysis import analyze_visa_history


DISCLOSURE_FILE = Path("data/reference/LCA_Disclosure_Data_FY2025_Q4.csv.gz")


if __name__ == "__main__":
    results = analyze_visa_history(DISCLOSURE_FILE)
    for status, count in sorted(results.items()):
        print(f"{status}: {count}")
