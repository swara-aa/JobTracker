from __future__ import annotations

from functools import lru_cache
import os
import re


SKILL_ALIASES = {
    "Machine Learning": ("machine learning", "ml", "deep learning"),
    "JavaScript": ("javascript", "js", "es6", "ecmascript"),
    "TypeScript": ("typescript", "ts"),
    "REST APIs": ("rest api", "restful api", "rest APIs", "api development"),
    "Backend Development": ("backend", "back-end", "server-side"),
    "Cloud": ("aws", "azure", "gcp", "cloud computing"),
    "LLMs": ("llm", "large language model", "generative ai", "genai"),
    "CI/CD": ("ci/cd", "continuous integration", "continuous delivery"),
    "MLOps": ("mlops", "machine learning operations", "model operations"),
    "Data Processing": ("data pipelines", "etl", "data processing"),
}


def extract_skills(text: str, known_patterns: dict[str, str]) -> set[str]:
    found = {name for name, pattern in known_patterns.items() if re.search(pattern, text, re.I)}
    normalized = text.lower()
    for skill, aliases in SKILL_ALIASES.items():
        if any(re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", normalized) for alias in aliases):
            found.add(skill)
    return found


def required_and_preferred_text(description: str) -> tuple[str, str]:
    required, preferred = [], []
    for sentence in re.split(r"(?<=[.!;])\s+|\n+", description):
        target = preferred if re.search(r"preferred|bonus|nice to have|plus", sentence, re.I) else required
        target.append(sentence)
    return " ".join(required), " ".join(preferred)


def semantic_similarity(resume_text: str, job_text: str) -> int | None:
    if os.getenv("JOB_AGENT_ENABLE_LOCAL_EMBEDDINGS", "0") != "1":
        return None
    try:
        model = _embedding_model()
        vectors = model.encode([resume_text[:20000], job_text[:20000]], normalize_embeddings=True)
        cosine = max(0.0, float(vectors[0] @ vectors[1]))
        return round(cosine * 100)
    except Exception:
        return None


@lru_cache(maxsize=1)
def _embedding_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer("all-MiniLM-L6-v2")
