from __future__ import annotations

import json
import logging
import os
from typing import List

from core.groq_client import GroqSettings, call_groq_text
from core.models.verifier import FieldVerification
from core.utils.json_cleaner import strip_code_fences

KEY_FIELDS = [
    "role",
    "required_skills",
    "work_mode",
    "experience_required",
]

logger = logging.getLogger("doc_intelligence.verifier")


def build_verifier_prompt(jd_text: str, extracted_json: str) -> str:
    return f"""
You are a strict verifier.

Given:
1) Job description text
2) Extracted structured data

Check if each field is correctly supported by the JD.

Rules:
- Mark verified=true only if clearly supported
- If not found or uncertain -> verified=false
- Provide a short exact quote from JD as evidence
- Keep output STRICT JSON

Fields to verify:
role, required_skills, work_mode, experience_required

JD:
{jd_text}

Extracted:
{extracted_json}

Return JSON array:
[
  {{
    "field": "role",
    "verified": true,
    "evidence_quote": "..."
  }}
]
""".strip()

def run_verifier(jd_text: str, extracted: dict, request_id: str) -> List[FieldVerification]:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
    if not api_key:
        raise RuntimeError("Missing GROQ_API_KEY.")

    minimal = {k: extracted.get(k) for k in KEY_FIELDS}
    prompt = build_verifier_prompt(jd_text=jd_text, extracted_json=json.dumps(minimal, ensure_ascii=True))
    raw = call_groq_text(GroqSettings(api_key=api_key, model=model), prompt, timeout_s=60)
    logger.info("verifier_raw request_id=%s raw=%s", request_id, raw)
    cleaned = strip_code_fences(raw)
    logger.info("verifier_cleaned request_id=%s cleaned=%s", request_id, cleaned[:500])

    parsed = json.loads(cleaned)
    if not isinstance(parsed, list):
        raise ValueError("Verifier output must be a JSON list.")

    return [FieldVerification.model_validate(item) for item in parsed]

