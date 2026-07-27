from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class JobPosting:
    source: str
    role_query: str
    title: str
    company: str
    location: str
    posting_date: datetime
    link: str
    salary: str = ""
    workplace_type: str = ""
    employment_type: str = ""
    applicant_count: str = ""
    easy_apply: bool = False
    description: str = ""

    @property
    def posting_date_iso(self) -> str:
        return self.posting_date.isoformat()
