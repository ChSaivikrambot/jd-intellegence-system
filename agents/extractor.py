"""Simplified JD Extractor - High recall raw skill extraction.

This extractor:
1. Uses LLM to extract ALL skills mentioned in JD (high recall)
2. Returns raw strings (no hardcoded skill lists)
3. Does NOT classify required vs preferred — that is the parser's job
4. Canonicalization happens AFTER extraction in a separate layer
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from app.schemas import JdAnalysisPayload
from core.groq_client import GroqSettings, call_groq_text
from core.utils.json_cleaner import strip_code_fences

logger = logging.getLogger("doc_intelligence.extractor")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_JD_CHARS = 6000
SKILLS_SOURCE = "llm_extracted"

# ---------------------------------------------------------------------------
# Prompt — pure extraction, no classification
# ---------------------------------------------------------------------------

_SCHEMA_HINT = {
    "role": "string or null",
    "skills": ["string — ALL technical skills mentioned in JD (required + preferred + optional)"],
}

_SKILL_RULES = (
    "\nExtract technical skills using specific, canonical names only.\n\n"
    "CRITICAL RULES:\n"
    "- Extract ONLY concrete tool/framework/language names. 1-3 words maximum.\n"
    "- Use the EXACT tool name, never a description of it.\n"
    "- NEVER extract umbrella phrases. See BAD/GOOD examples below.\n"
    "- ALWAYS extract the specific skill named in parenthetical examples.\n"
    "- Extract ALL technical skills mentioned — do NOT decide importance.\n"
    "- Return a single list: skills. Include ALL mentioned technologies, even if optional.\n\n"

    "BAD vs GOOD extraction examples (memorize these patterns):\n"
    "  BAD: 'C/C++ programming skills'          GOOD: 'C++'\n"
    "  BAD: 'AWS services management'           GOOD: 'AWS'\n"
    "  BAD: 'cloud-native CI/CD pipelines'      GOOD: 'CI/CD'\n"
    "  BAD: 'android display/graphics framework' GOOD: 'Android'\n"
    "  BAD: 'code revision control systems'     GOOD: 'Git'\n"
    "  BAD: 'LLM frameworks'                    GOOD: extract the named example: 'LangChain'\n"
    "  BAD: 'Gen AI tools'                      GOOD: extract named examples: 'LangChain', 'RAG'\n"
    "  BAD: 'cloud platforms'                   GOOD: extract named examples: 'GCP', 'AWS'\n"
    "  BAD: 'programming languages'             GOOD: extract named examples: 'Go', 'Python', 'Java'\n"
    "  BAD: 'machine learning/deep learning'    GOOD: 'Deep Learning' (or named tools)\n"
    "  BAD: 'operating systems'                 GOOD: extract named examples: 'Linux', 'Windows'\n"
    "  BAD: 'unix operating system'             GOOD: 'Unix'\n"
    "  BAD: 'object oriented concepts'          GOOD: skip (not a skill name)\n"
    "  BAD: 'identity management'               GOOD: skip (not a concrete tool)\n\n"

    "Parenthetical rule: If JD writes 'Category (e.g., X, Y)', ALWAYS extract X and Y, NOT the category.\n"
    "  Example: 'LLM frameworks (e.g., LangChain)' → extract 'LangChain'\n"
    "  Example: 'messaging services (Kafka, SQS, SNS, Kinesis)' → extract: Kafka, SQS, SNS, Kinesis\n"
    "  Example: 'cloud services (GCP or AWS)' → extract: GCP, AWS\n\n"

    "HIGH-RECALL RULE:\n"
    "- Do NOT classify.\n"
    "- Do NOT decide importance.\n"
    "- Return ALL skills in a single 'skills' array.\n"
    "- Parser will decide which are required vs preferred later.\n"
)

_STRICT_RULES = (
    "\nStrict output rules:\n"
    "- Return ONLY a JSON object. No markdown, no preamble.\n"
    "- Start your response with { directly.\n"
    "- Use null for unknown fields, never omit them.\n"
    "- skills must be a JSON array of strings (all technical skills).\n"
    "- Never omit skills field — it must always be present as an array.\n"
)


def _build_prompt(jd_text: str, *, strict: bool) -> str:
    """Build simple extraction prompt."""
    strict_block = _STRICT_RULES if strict else "\nReturn ONLY a JSON object. No markdown, no extra text.\n"

    return (
        "Extract the following fields from this job description into JSON.\n\n"
        f"Target schema:\n{json.dumps(_SCHEMA_HINT, indent=2)}\n"
        f"{_SKILL_RULES}"
        f"{strict_block}\n"
        "Job Description:\n"
        f"{jd_text}\n"
    )


# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------

def _is_valid_skill(s: str) -> bool:
    """Filter out long phrases and non-technical skills."""
    if not s or not isinstance(s, str):
        return False

    s = s.strip()
    if not s:
        return False

    # Max 4 words for skill names
    word_count = len(s.split())
    if word_count > 4:
        return False

    description_indicators = [
        "management", "development", "programming", "technologies",
        "framework", "concepts", "improvement", "architecture",
        "system", "tools", "platforms", "integration",
    ]

    EXACT_WHITELIST = {
        "spring boot", "react native", "node.js", "ci/cd",
        "rtos",
        "drm/kms",
    }

    s_lower = s.lower()

    if s_lower in EXACT_WHITELIST:
        pass
    else:
        for term in description_indicators:
            if term in s_lower:
                return False

    if s_lower.startswith("cloud native"):
        return False

    VALID_SLASH_SKILLS = {"c/c++", "ci/cd", "drm/kms", "tcp/ip", "i/o"}
    if "/" in s_lower and s_lower not in VALID_SLASH_SKILLS:
        parts = s_lower.split("/")
        if len(parts) == 2 and all(len(p.strip()) > 2 for p in parts) and len(s) > 6:
            return False

    blacklist = [
        "bachelor", "master", "degree", "phd",
        "certified", "certification",
    ]

    for term in blacklist:
        if term in s_lower:
            return False

    return True


def _sanitize(data: Dict[str, Any], attempt: int) -> Dict[str, Any]:
    """
    Clean up LLM output before validation.

    The extractor owns ONLY:
        - role
        - skills (unified list — parser will classify into required/preferred)

    The extractor does NOT populate required_skills or preferred_skills.
    That is the parser's job exclusively.
    """
    # Pipeline-controlled fields
    data["skills_source"] = SKILLS_SOURCE
    data["retries_used"] = max(0, attempt - 1)

    # Process the unified skills field — filter bad extractions
    skills = data.get("skills", [])
    if isinstance(skills, list):
        data["skills"] = [s for s in skills if _is_valid_skill(s)]
    else:
        data["skills"] = []

    # Ensure required_skills and preferred_skills are empty lists.
    # Pipeline (via classify_skills) will populate them — not the extractor.
    data["required_skills"] = []
    data["preferred_skills"] = []

    # Ensure list fields are always lists
    for field_name in ("skills", "required_skills", "preferred_skills"):
        if not isinstance(data.get(field_name), list):
            data[field_name] = []

    return data


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_extractor(
    jd_text: str,
    *,
    request_id: str,
    attempt: int,
) -> Tuple[Optional[JdAnalysisPayload], str]:
    """
    Stage 2A: JD text → LLM extraction → sanitize.

    High-recall extraction: LLM extracts ALL mentioned technologies.
    Canonicalization and classification happen in separate layers AFTER this.

    Returns (payload_or_none, raw_llm_text).
    """
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
    if not api_key:
        raise RuntimeError("Missing GROQ_API_KEY — check environment config.")

    # Truncate oversized JDs
    original_len = len(jd_text)
    if original_len > MAX_JD_CHARS:
        logger.warning(
            "extractor_truncated request_id=%s original_len=%d truncated_to=%d",
            request_id, original_len, MAX_JD_CHARS,
        )
        jd_text = jd_text[:MAX_JD_CHARS]

    # Call LLM for extraction
    settings = GroqSettings(api_key=api_key, model=model)
    prompt = _build_prompt(jd_text, strict=attempt >= 2)
    raw = call_groq_text(settings, prompt, timeout_s=60)

    logger.info(
        "extractor_raw request_id=%s attempt=%d model=%s raw_preview=%.300s",
        request_id, attempt, model, raw,
    )

    cleaned_output = strip_code_fences(raw)
    logger.info(
        "extractor_cleaned request_id=%s attempt=%d cleaned_preview=%.500s",
        request_id, attempt, cleaned_output,
    )

    # JSON parse
    try:
        data: Dict[str, Any] = json.loads(cleaned_output)
    except json.JSONDecodeError as exc:
        logger.warning(
            "extractor_json_parse_failed request_id=%s attempt=%d error=%s",
            request_id, attempt, exc,
        )
        return None, raw

    # Sanitize
    data = _sanitize(data, attempt)

    logger.debug(
        "extractor_sanitized request_id=%s attempt=%d skills=%s",
        request_id, attempt,
        data.get("skills"),
    )

    # Pydantic validate
    try:
        payload = JdAnalysisPayload.model_validate(data)
    except ValidationError as exc:
        logger.warning(
            "extractor_validation_failed request_id=%s attempt=%d error_count=%d errors=%s",
            request_id, attempt, len(exc.errors()), exc.errors(),
        )
        return None, raw

    return payload, raw