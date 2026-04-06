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
    "required_one_of",
    "work_mode",
    "experience_required",
]

logger = logging.getLogger("doc_intelligence.verifier")

ATOMIC_SKILL_RULES = (
    "Skill format rules (CRITICAL):\n"
    "- Each skill must be ONE atomic name only.\n"
    "- Use short standard forms: 'CI/CD' not 'CI/CD pipelines', 'Microservices' not 'microservices architecture', 'DSA' not 'Data Structures and Algorithms'.\n"
    "- Never group multiple skills into one string.\n"
    "- Never add qualifier words like 'pipelines', 'architecture', 'concepts', 'fundamentals'.\n"
)


def build_verifier_prompt(jd_text: str, extracted_json: str) -> str:
    return f"""
You are a strict verifier.

Given:
1) Job description text
2) Extracted structured data

Check if each field is correctly supported by the JD.

Rules:
- Mark verified=true only if clearly supported by the JD text
- If not found or uncertain -> verified=false
- Provide a short exact quote from JD as evidence
- For required_skills: verify=true if the JD mentions those skill areas, even if phrased differently
- required_skills can be an empty list ([]) — this is valid when all skills are captured in required_one_of. Mark verified=true if required_skills is [] and required_one_of is populated.
- required_one_of can be an empty list ([]) — this is valid when all skills are in required_skills. Mark verified=true if required_one_of is [] and required_skills is populated.
- Keep output STRICT JSON

{ATOMIC_SKILL_RULES}

Fields to verify:
{', '.join(KEY_FIELDS)}

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


def build_correction_prompt(jd_text: str, current_payload: str, failed_fields: List[str]) -> str:
    return f"""
You are correcting specific fields in a structured extraction.

Rules:
- Only return the requested fields
- Do NOT include other fields
- Use only information from the JD
- If not present, return null
- Do NOT expand or add skills not in the current extracted data
- Do NOT revert atomic skill names to verbose JD phrasing
- Do NOT move skills from required_one_of into required_skills. If required_skills is empty because skills are in required_one_of, return: {{"required_skills": []}}
- Do NOT add any skill that is not explicitly mentioned in the JD text.

{ATOMIC_SKILL_RULES}

Fields to correct: {failed_fields}

JD:
{jd_text}

Current extracted data:
{current_payload}

Return STRICT JSON:
{{
  "field_name": value
}}
""".strip()


def run_correction(jd_text: str, extracted: dict, failed_fields: List[str], request_id: str) -> dict:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
    
    prompt = build_correction_prompt(jd_text, json.dumps(extracted, ensure_ascii=True), failed_fields)
    raw = call_groq_text(GroqSettings(api_key=api_key, model=model), prompt, timeout_s=60)
    logger.info("correction_raw request_id=%s raw=%s", request_id, raw)
    
    cleaned = strip_code_fences(raw)
    logger.info("correction_cleaned request_id=%s cleaned=%s", request_id, cleaned[:500])
    
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("Correction output must be a JSON object.")
        
    return parsed