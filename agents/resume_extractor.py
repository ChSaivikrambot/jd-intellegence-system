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
    return f"""
Extract only technical/professional skills from this resume.

Rules:
- Return STRICT JSON object with one key: "skills".
- "skills" must be an array of short skill names.
- Include tools, languages, frameworks, databases, cloud, devops.
- Exclude soft skills and generic adjectives.
- Do not invent anything not present in resume.
- Keep deduplicated and concise.

Resume text (markdown):
{resume_markdown}

Return JSON:
{{
  "skills": ["Python", "FastAPI", "Postgres"]
}}
""".strip()


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
