from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import sqlite3
from typing import Any

from job_agent.config import DB_PATH
from job_agent.local_scorer import extract_skills, required_and_preferred_text, semantic_similarity
from job_agent.storage import ensure_database, fetch_jobs, fetch_resumes


SKILL_TERMS = {
    "Python": r"\bpython\b",
    "Java": r"\bjava\b",
    "JavaScript": r"\bjavascript\b|\bjs\b",
    "TypeScript": r"\btypescript\b",
    "C++": r"\bc\+\+\b",
    "C#": r"\bc#\b|\bc sharp\b",
    "SQL": r"\bsql\b",
    "Git": r"\bgit\b",
    "Linux": r"\blinux\b",
    "Docker": r"\bdocker\b",
    "Kubernetes": r"\bkubernetes\b|\bk8s\b",
    "AWS": r"\baws\b|amazon web services",
    "Azure": r"\bazure\b",
    "GCP": r"\bgcp\b|google cloud",
    "React": r"\breact\b",
    "Node.js": r"\bnode\.?js\b",
    "Flask": r"\bflask\b",
    "FastAPI": r"\bfastapi\b",
    "Django": r"\bdjango\b",
    "REST APIs": r"\brest(?:ful)? api",
    "Machine Learning": r"\bmachine learning\b",
    "Deep Learning": r"\bdeep learning\b",
    "LLMs": r"\bllms?\b|large language models?",
    "PyTorch": r"\bpytorch\b",
    "TensorFlow": r"\btensorflow\b",
    "Pandas": r"\bpandas\b",
    "NumPy": r"\bnumpy\b",
    "Scikit-learn": r"\bscikit[ -]learn\b",
    "Data Structures": r"\bdata structures?\b",
    "Algorithms": r"\balgorithms?\b",
    "CI/CD": r"\bci/?cd\b|continuous integration",
    "MLOps": r"\bmlops\b|machine learning operations",
    "Model Deployment": r"\bmodel deployment\b|deploy(?:ing|ment) of models?\b",
    "Model Evaluation": r"\bmodel evaluation\b|evaluat(?:e|ing|ion) models?\b",
    "Data Processing": r"\bdata processing\b|data pipelines?\b",
    "Agile": r"\bagile\b|scrum",
}
FOUNDATIONAL_SKILLS = {"Algorithms", "Data Structures", "Git", "Linux", "Agile"}
ROLE_TERMS = {
    "software": {"software", "backend", "frontend", "fullstack", "full-stack", "developer"},
    "ai_ml": {"ai", "machine learning", "ml", "data science", "deep learning", "llm"},
}
HARD_NO_PATTERNS = {
    "U.S. citizenship is explicitly required": r"u\.?s\.? citizenship (?:is )?required",
    "An active security clearance is explicitly required": r"(?:active|current) (?:security )?clearance (?:is )?required",
    "The posting requires permanent work authorization without sponsorship": r"(?:no|without) (?:visa )?sponsorship(?: now or in the future)?|must (?:be )?(?:permanently )?authorized to work.*(?:without|no).*sponsorship",
}


def score_all_jobs_locally() -> dict[str, int]:
    resumes = fetch_resumes()
    if not resumes:
        raise ValueError("Upload at least one resume before running local pre-scores.")

    jobs = fetch_jobs()
    _store_local_scores(jobs, resumes)
    return {"scored": len(jobs), "resumes": len(resumes)}


def score_jobs_locally(job_ids: list[int]) -> dict[str, int]:
    resumes = fetch_resumes()
    if not resumes:
        return {"scored": 0, "resumes": 0}
    requested_ids = {int(job_id) for job_id in job_ids}
    jobs = [job for job in fetch_jobs() if int(job["id"]) in requested_ids]
    _store_local_scores(jobs, resumes)
    return {"scored": len(jobs), "resumes": len(resumes)}


def _store_local_scores(jobs: list[dict[str, Any]], resumes: list[dict[str, object]]) -> None:
    scored_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as connection:
        for job in jobs:
            result = _best_resume_score(job, resumes)
            connection.execute(
                """
                UPDATE jobs
                SET local_match_score = ?, local_match_resume_id = ?,
                    local_match_evidence = ?, local_match_missing = ?,
                    local_match_hard_no = ?, local_match_hard_no_reasons = ?,
                    local_match_analyzed_at = ?, local_semantic_score = ?
                WHERE id = ?
                """,
                (
                    result["score"],
                    result["resume_id"],
                    json.dumps(result["evidence"]),
                    json.dumps(result["missing"]),
                    int(result["hard_no"]),
                    json.dumps(result["hard_no_reasons"]),
                    scored_at,
                    result["semantic_score"],
                    job["id"],
                ),
            )
        connection.commit()


def _best_resume_score(
    job: dict[str, Any],
    resumes: list[dict[str, object]],
) -> dict[str, Any]:
    scores = [_score_resume(job, resume) for resume in resumes]
    return max(scores, key=lambda item: int(item["score"]))


def _score_resume(job: dict[str, Any], resume: dict[str, object]) -> dict[str, Any]:
    job_text = _job_text(job)
    resume_text = str(resume["content"]).lower()
    hard_no_reasons = [
        reason for reason, pattern in HARD_NO_PATTERNS.items() if re.search(pattern, job_text, re.I)
    ]
    if hard_no_reasons:
        return {
            "score": 0,
            "resume_id": int(resume["id"]),
            "evidence": [],
            "missing": [],
            "hard_no": True,
            "hard_no_reasons": hard_no_reasons,
            "semantic_score": None,
        }

    job_skills = extract_skills(job_text, SKILL_TERMS)
    resume_skills = extract_skills(resume_text, SKILL_TERMS)
    matched_skills = sorted(job_skills.intersection(resume_skills))
    missing_skills = sorted(job_skills.difference(resume_skills))
    required_text, preferred_text = required_and_preferred_text(str(job.get("description") or ""))
    required_skills = extract_skills(required_text, SKILL_TERMS) or job_skills
    preferred_skills = extract_skills(preferred_text, SKILL_TERMS).difference(required_skills)
    skill_score = round(0.8 * _weighted_skill_score(required_skills, sorted(required_skills & resume_skills)))
    skill_score += round(0.2 * _weighted_skill_score(preferred_skills, sorted(preferred_skills & resume_skills))) if preferred_skills else 0

    role_score = _role_alignment_score(str(job.get("title") or ""), resume_text)
    overlap_score = _keyword_overlap_score(job_text, resume_text)
    evidence_score = _evidence_score(matched_skills)
    requirement_gaps, requirement_penalty = _requirement_gaps(job_text, resume_text)
    missing_items = sorted(set(missing_skills + requirement_gaps))
    score = max(0, skill_score + role_score + overlap_score + evidence_score - requirement_penalty)
    semantic_score = semantic_similarity(resume_text, job_text)
    if semantic_score is not None:
        score = round(0.6 * score + 0.4 * semantic_score)
    score = min(score, _score_cap(job, job_skills, matched_skills))
    return {
        "score": score,
        "resume_id": int(resume["id"]),
        "evidence": matched_skills,
        "missing": missing_items,
        "hard_no": False,
        "hard_no_reasons": [],
        "semantic_score": semantic_score,
    }


def _job_text(job: dict[str, Any]) -> str:
    return " ".join(
        str(job.get(field) or "")
        for field in [
            "title",
            "role_query",
            "description",
            "gemini_skills_required",
            "gemini_skills_preferred",
            "gemini_requirements",
        ]
    ).lower()


def _role_alignment_score(title: str, resume_text: str) -> int:
    title_lower = title.lower()
    relevant_groups = [
        terms for role, terms in ROLE_TERMS.items() if any(term in title_lower for term in terms)
    ]
    if not relevant_groups:
        return 0
    matches = sum(any(term in resume_text for term in terms) for terms in relevant_groups)
    return round(15 * matches / len(relevant_groups))


def _keyword_overlap_score(job_text: str, resume_text: str) -> int:
    job_words = {word for word in re.findall(r"[a-z][a-z0-9+#.-]{2,}", job_text) if word not in _STOP_WORDS}
    resume_words = set(re.findall(r"[a-z][a-z0-9+#.-]{2,}", resume_text))
    if not job_words:
        return 0
    return min(8, round(8 * len(job_words.intersection(resume_words)) / min(len(job_words), 80)))


def _weighted_skill_score(job_skills: set[str], matched_skills: list[str]) -> int:
    if not job_skills:
        return 0
    matched = set(matched_skills)
    total_weight = sum(0.45 if skill in FOUNDATIONAL_SKILLS else 1.0 for skill in job_skills)
    matched_weight = sum(0.45 if skill in FOUNDATIONAL_SKILLS else 1.0 for skill in matched)
    return round(55 * matched_weight / total_weight)


def _evidence_score(matched_skills: list[str]) -> int:
    specialized_matches = [skill for skill in matched_skills if skill not in FOUNDATIONAL_SKILLS]
    if len(specialized_matches) >= 4:
        return 12
    if len(specialized_matches) >= 2:
        return 6
    return 0


def _requirement_gaps(job_text: str, resume_text: str) -> tuple[list[str], int]:
    gaps: list[str] = []
    penalty = 0
    years_match = re.search(r"\b(\d+)\+?\s+years?\s+of\s+(?:professional\s+)?experience\b", job_text)
    if years_match:
        required_years = int(years_match.group(1))
        resume_years = [int(value) for value in re.findall(r"\b(\d+)\+?\s+years?\b", resume_text)]
        if not resume_years or max(resume_years) < required_years:
            gaps.append(f"{required_years}+ years of required experience is not clearly evidenced")
            penalty += 15
    if re.search(r"\b(?:bachelor['’]?s|bachelor)\s+degree\b", job_text) and not re.search(
        r"\b(?:bachelor['’]?s|bachelor|b\.?s\.?|b\.?tech)\b", resume_text
    ):
        gaps.append("Bachelor's degree requirement is not clearly evidenced")
        penalty += 10
    return gaps, penalty


def _score_cap(job: dict[str, Any], job_skills: set[str], matched_skills: list[str]) -> int:
    if not str(job.get("description") or "").strip():
        return 45
    specialized_job_skills = job_skills.difference(FOUNDATIONAL_SKILLS)
    specialized_matches = set(matched_skills).difference(FOUNDATIONAL_SKILLS)
    if not specialized_job_skills:
        return 55
    if len(specialized_matches) < 2:
        return 65
    if len(specialized_matches) < 4:
        return 78
    return 90


_STOP_WORDS = {
    "and", "are", "the", "for", "with", "you", "your", "this", "that", "will", "from",
    "have", "our", "who", "job", "role", "work", "team", "years", "experience", "skills",
    "required", "preferred", "including", "using", "about", "their", "they", "all", "new",
}
