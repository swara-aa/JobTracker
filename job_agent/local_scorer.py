from __future__ import annotations

from functools import lru_cache
import os
import re


SKILL_ALIASES = {
    "Software Engineering": ("software engineer", "software development", "application development", "build software"),
    "Artificial Intelligence": ("artificial intelligence", "ai engineering", "ai application", "ai agents"),
    "Machine Learning": ("machine learning", "ml", "deep learning", "predictive modeling"),
    "JavaScript": ("javascript", "js", "es6", "ecmascript"),
    "TypeScript": ("typescript", "ts", "typed javascript"),
    "REST APIs": ("rest api", "restful api", "rest APIs", "api development", "web services"),
    "Backend Development": ("backend", "back-end", "server-side", "application development"),
    "Frontend Development": ("frontend", "front-end", "web development", "web applications"),
    "Full-Stack Development": ("full stack", "full-stack", "end-to-end application"),
    "Cloud": ("aws", "azure", "gcp", "cloud computing", "cloud-based platforms", "cloud platforms"),
    "LLMs": ("llm", "large language model", "generative ai", "genai"),
    "CI/CD": ("ci/cd", "continuous integration", "continuous delivery"),
    "MLOps": ("mlops", "machine learning operations", "model operations"),
    "Data Processing": ("data pipelines", "etl", "data processing", "data analysis", "data handling"),
    "Data Engineering": ("data engineering", "data platform", "data infrastructure"),
    "Infrastructure": ("infrastructure engineering", "platform engineering", "site reliability", "production environments"),
    "Testing": ("testing", "test automation", "quality assurance", "validate"),
    "Problem Solving": ("problem solving", "problem-solving", "analytical thinking", "analytical skills"),
    "Technical Documentation": ("technical documentation", "documentation", "technical writing"),
    "Research": ("research", "research assistant", "experimentation"),
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
        vectors = model.encode(
            [_semantic_text(resume_text), _semantic_text(job_text)],
            normalize_embeddings=True,
        )
        cosine = max(0.0, float(vectors[0] @ vectors[1]))
        return round(min(100.0, max(0.0, (cosine - 0.2) / 0.6 * 100)))
    except Exception:
        return None


@lru_cache(maxsize=1)
def _embedding_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer("all-MiniLM-L6-v2")


def _semantic_text(text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    useful_sentences = [
        sentence.strip()
        for sentence in sentences
        if 25 <= len(sentence.strip()) <= 500
    ]
    return " ".join(useful_sentences[:60])[:12000]
