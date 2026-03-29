from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional, Tuple

from pydantic import ValidationError

from app.schemas import JdAnalysisPayload
from core.groq_client import GroqSettings, call_groq_text
from core.normalization import normalize_work_mode
from core.utils.json_cleaner import strip_code_fences


logger = logging.getLogger("doc_intelligence.extractor")


def _build_prompt(jd_text: str, *, strict: bool) -> str:
    schema_hint = {
        "company": "string or null",
        "role": "string or null",
        "truly_entry_level": "boolean or null",
        "required_skills": ["string"],
        "preferred_skills": ["string"],
        "experience_required": "string or null",
        "red_flags": ["string"],
        "compensation": "string or null",
        "work_mode": "remote|hybrid|onsite|not_mentioned|null",
    }
    extra_rules = ""
    if strict:
        extra_rules = (
            "\nRules:\n"
            "- Return ONLY a JSON object, no markdown, no extra text.\n"
            "- Use null when unknown.\n"
            "- required_skills/preferred_skills/red_flags must be JSON arrays of strings.\n"
            "- work_mode must be one of: remote, hybrid, onsite, not_mentioned (or null).\n"
        )

    return (
        "Extract job description fields into JSON.\n"
        f"Target JSON keys and types: {json.dumps(schema_hint)}\n"
        f"{extra_rules}\n"
        "Job Description:\n"
        f"{jd_text}\n"
    )


def run_extractor(jd_text: str, *, request_id: str, attempt: int) -> Tuple[Optional[JdAnalysisPayload], str]:
    """
    Stage 2A: JD text -> LLM -> parse JSON -> normalize -> Pydantic validate.

    Returns (payload_or_none, raw_llm_text). Caller owns retry policy.
    """
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
    if not api_key:
        raise RuntimeError("Missing GROQ_API_KEY.")

    settings = GroqSettings(api_key=api_key, model=model)
    prompt = _build_prompt(jd_text, strict=attempt > 1)
    raw = call_groq_text(settings, prompt, timeout_s=60)

    # Always log raw output (critical for debugging).
    logger.info("extractor_raw request_id=%s attempt=%s model=%s raw=%s", request_id, attempt, model, raw)

    cleaned_output = strip_code_fences(raw)
    logger.info(
        "extractor_cleaned request_id=%s attempt=%s cleaned=%s",
        request_id,
        attempt,
        cleaned_output[:500],
    )

    try:
        data: Dict[str, Any] = json.loads(cleaned_output)
    except json.JSONDecodeError:
        return None, raw

    # Normalize before validation (schema strict, input flexible).
    if "work_mode" in data:
        data["work_mode"] = normalize_work_mode(data.get("work_mode")) or data.get("work_mode")

    # Ensure provenance stays stable for this endpoint.
    data["skills_source"] = "manual"
    data.setdefault("retries_used", max(0, attempt - 1))

    try:
        payload = JdAnalysisPayload.model_validate(data)
    except ValidationError:
        return None, raw

    return payload, raw

