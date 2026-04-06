from __future__ import annotations

import json
import logging
import os
from typing import List

from core.groq_client import GroqSettings, call_groq_text
from core.utils.json_cleaner import strip_code_fences

logger = logging.getLogger("doc_intelligence.resume_extractor")


def _canon(s: str) -> str:
    return " ".join(s.strip().lower().split())


def _clean_skills(values: list) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in values:
        text = str(raw).strip()
        if not text:
            continue
        key = _canon(text)
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def build_resume_skills_prompt(resume_markdown: str) -> str:
    return f"""Extract only technical skills the candidate has PRACTICAL EXPERIENCE with.

STRICT RULES:
1. ONLY include skills used in projects, work experience, or education with practical application
2. DO NOT include skills mentioned only in "Languages" or "Skills" sections without context
3. DO NOT infer skills - if resume says "familiar with Java" → DO NOT include Java
4. DO NOT include skills from coursework unless explicitly used in projects

Examples of GOOD extraction:
- "Built REST API using FastAPI and PostgreSQL" → ["FastAPI", "PostgreSQL"]
- "Developed React frontend with TypeScript" → ["React", "TypeScript"]

Examples of BAD extraction:
- Resume lists "Languages: C++, Java, Python" with no context → []
- "Familiar with machine learning concepts" → []

Return STRICT JSON with one key "skills":
{{
  "skills": ["Python", "FastAPI", "React"]
}}

Resume text (markdown):
{resume_markdown}
"""


def run_resume_skill_extractor(resume_markdown: str, request_id: str) -> List[str]:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
    if not api_key:
        raise RuntimeError("Missing GROQ_API_KEY.")

    prompt = build_resume_skills_prompt(resume_markdown)
    raw = call_groq_text(GroqSettings(api_key=api_key, model=model), prompt, timeout_s=60)
    logger.info("resume_extractor_raw request_id=%s raw=%s", request_id, raw)

    cleaned = strip_code_fences(raw)
    logger.info("resume_extractor_cleaned request_id=%s cleaned=%s", request_id, cleaned[:500])
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("Resume extractor output must be a JSON object.")

    skills = parsed.get("skills", [])
    if not isinstance(skills, list):
        raise ValueError("Resume extractor output 'skills' must be a JSON list.")

    return _clean_skills(skills)
