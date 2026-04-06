"""Shared pipeline orchestration for JD Intelligence.

This module provides the complete pipeline used by both the API and eval runner.
Ensures consistency between production and evaluation paths.
"""

from __future__ import annotations

import logging
from typing import List, Tuple

from agents.extractor import run_extractor
from agents.verifier import run_verifier, run_correction
from core.cleaning import clean_jd_text
from core.decision import apply_stage2b_decisions
from core.jd_parser import parse as parse_jd, classify_skills
from core.skill_canonicalizer import canonicalize_list, canonicalize_pools, get_skill_weight

logger = logging.getLogger("doc_intelligence.pipeline")


class PipelineResult:
    """Result container for pipeline execution."""
    
    def __init__(
        self,
        payload: any,
        warnings: list,
        verifications: list,
        retries_used: int = 0,
    ):
        self.payload = payload
        self.warnings = warnings
        self.verifications = verifications
        self.retries_used = retries_used


def run_full_pipeline(
    jd_text: str,
    user_skills: List[str],
    *,
    request_id: str = "",
    max_extractor_attempts: int = 3,
    apply_verifier: bool = True,
    apply_smart_retry: bool = True,
) -> PipelineResult:
    """
    Run the complete JD analysis pipeline.
    
    Stages:
    1. Text cleaning
    2. Extraction (LLM - role, required_skills, preferred_skills)
    2.5 JD Parser (rule-based - pools, experience, red_flags, work_mode)
    3. Canonicalization
    4. Decision engine
    5. Verification
    6. Smart retry (if needed)
    7. Re-decision (if corrections applied)
    
    Args:
        jd_text: Raw JD text
        user_skills: Candidate skills
        request_id: Request identifier for logging
        max_extractor_attempts: Max attempts for extraction
        apply_verifier: Whether to run verifier stage
        apply_smart_retry: Whether to run smart retry on failures
    
    Returns:
        PipelineResult with final payload, warnings, and metadata
    """
    from app.schemas import WarningItem  # Import here to avoid circular deps
    
    warnings: List[WarningItem] = []
    
    # Stage 1: Clean text
    try:
        cleaned_text = clean_jd_text(jd_text)
    except ValueError as e:
        logger.error("pipeline_cleaning_failed request_id=%s error=%s", request_id, str(e))
        raise
    
    logger.info(
        "pipeline_start request_id=%s jd_chars=%d skills_count=%d",
        request_id, len(cleaned_text), len(user_skills)
    )
    
    # Stage 2: Extraction
    payload = None
    last_raw = ""
    for attempt in range(1, max_extractor_attempts + 1):
        try:
            payload, last_raw = run_extractor(
                cleaned_text,
                request_id=request_id,
                attempt=attempt
            )
        except Exception as e:
            logger.error(
                "pipeline_extractor_error request_id=%s attempt=%d error=%s",
                request_id, attempt, str(e)
            )
            if attempt == max_extractor_attempts:
                raise RuntimeError(f"Extractor failed after {max_extractor_attempts} attempts: {e}")
            continue
        if payload is not None:
            break
    
    if payload is None:
        raise RuntimeError(f"Extractor returned None after {max_extractor_attempts} attempts")
    
    # Stage 2.5: JD Parser (rule-based fields — authoritative)
    # NEW: Parser now owns skill classification using extractor's unified skills list
    
    parsed = parse_jd(cleaned_text)
    
    # Rule-based fields override LLM extraction
    payload.work_mode = parsed.work_mode
    payload.truly_entry_level = parsed.truly_entry_level
    payload.experience_required = parsed.experience_required or payload.experience_required
    payload.red_flags = parsed.red_flags
    
    # NEW: Parser classifies skills using extractor's unified skills field
    # Get all skills from extractor (new path) or fallback to old fields
    all_skills = getattr(payload, 'skills', None) or []
    
    # Fallback: if skills is empty but old fields have data, use them
    if not all_skills:
        all_skills = (payload.required_skills or []) + (payload.preferred_skills or [])
    
    # Parser classifies skills into required vs preferred
    if all_skills:
        required, preferred = classify_skills(all_skills, cleaned_text)
        payload.required_skills = required
        payload.preferred_skills = preferred
    
    # Merge pools: parser pools are authoritative
    payload.required_one_of = parsed.required_one_of
    pool_flat = {s.lower() for pool in parsed.required_one_of for s in pool}
    payload.required_skills = [
        s for s in (payload.required_skills or [])
        if s.lower() not in pool_flat
    ]
    
    logger.info(
        "pipeline_jd_parser request_id=%s work_mode=%s entry=%s exp=%r flags=%d pools=%d",
        request_id,
        payload.work_mode,
        payload.truly_entry_level,
        payload.experience_required,
        len(payload.red_flags),
        len(payload.required_one_of),
    )
    
    # Stage 3: Canonicalization
    try:
        original_required = payload.required_skills or []
        original_one_of = payload.required_one_of or []
        original_preferred = payload.preferred_skills or []
        
        payload.required_skills = canonicalize_list(original_required)
        payload.required_one_of = canonicalize_pools(original_one_of)
        payload.preferred_skills = canonicalize_list(original_preferred)

        # Rebalance: if too many required skills, move lowest-weight to preferred
        # Only for non-entry-level JDs to avoid hiding real requirements
        if len(payload.required_skills) > 8 and not payload.truly_entry_level:
            # Sort by weight (lowest first), move excess to preferred
            sorted_skills = sorted(
                payload.required_skills,
                key=lambda s: get_skill_weight(s)
            )
            # Keep top 8 in required, move rest to preferred
            payload.required_skills = sorted_skills[-8:]  # highest weight kept
            moved_to_preferred = sorted_skills[:-8]  # lowest weight moved
            # Add to preferred (avoiding duplicates)
            existing_pref = {p.lower() for p in payload.preferred_skills}
            for skill in moved_to_preferred:
                if skill.lower() not in existing_pref:
                    payload.preferred_skills.append(skill)
            logger.info(
                "pipeline_rebalanced request_id=%s moved=%d to preferred",
                request_id, len(moved_to_preferred)
            )

        logger.info(
            "pipeline_canonicalization request_id=%s "
            "required=%d→%d pools=%d→%d preferred=%d→%d",
            request_id,
            len(original_required), len(payload.required_skills),
            len(original_one_of), len(payload.required_one_of),
            len(original_preferred), len(payload.preferred_skills),
        )
    except Exception as e:
        logger.error("pipeline_canonicalization_failed request_id=%s error=%s", request_id, str(e))
        warnings.append(
            WarningItem(
                code="CANONICALIZATION_FAILED",
                message="Skill canonicalization failed; proceeding with raw skills.",
                details={"error": str(e)},
            )
        )
    
    # Stage 4: Decision engine (with canonicalized user skills)
    canonical_user_skills = canonicalize_list(user_skills)
    payload = apply_stage2b_decisions(payload, canonical_user_skills, request_id=request_id)
    
    # Stage 5: Verification (optional)
    verifications = []
    if apply_verifier:
        try:
            verifications = run_verifier(
                cleaned_text,
                payload.model_dump(),
                request_id=request_id
            )
            _apply_verification_guardrails(payload, verifications, warnings)
            
            # Stage 6: Smart retry (if enabled and needed)
            if apply_smart_retry:
                # Remove verification warnings from outer list before retry,
                # because _run_smart_retry will re-verify and add fresh ones.
                warnings = [w for w in warnings if w.code not in ("VERIFICATION_FAILED", "VERIFICATION_EMPTY")]

                payload, verifications, retry_warnings, retries_used = _run_smart_retry(
                    cleaned_text,
                    payload,
                    verifications,
                    request_id=request_id,
                    canonical_user_skills=canonical_user_skills,
                )
                warnings.extend(retry_warnings)
                
        except Exception as e:
            logger.error("pipeline_verifier_failed request_id=%s error=%s", request_id, str(e))
            warnings.append(
                WarningItem(
                    code="VERIFIER_UNAVAILABLE",
                    message="Verifier step failed; returning unverified output.",
                    details={"error": str(e)},
                )
            )
            retries_used = 0
    else:
        retries_used = 0
    
    logger.info(
        "pipeline_complete request_id=%s recommendation=%s score=%s retries=%d warnings=%d",
        request_id,
        payload.recommendation,
        payload.match_score,
        retries_used,
        len(warnings)
    )
    
    return PipelineResult(
        payload=payload,
        warnings=warnings,
        verifications=verifications,
        retries_used=retries_used,
    )


def _apply_verification_guardrails(payload, verifications: list, warnings: list) -> None:
    """Apply confidence downgrades based on verification results."""
    from app.schemas import WarningItem
    
    CONFIDENCE_PRIORITY = {
        "failed": 0,
        "low": 1,
        "medium": 2,
        "high": 3,
    }
    
    def downgrade_confidence(current: str | None, new: str) -> str:
        if current is None:
            return new
        if CONFIDENCE_PRIORITY[new] < CONFIDENCE_PRIORITY[current]:
            return new
        return current
    
    payload.verification = verifications
    
    if not verifications:
        payload.confidence = downgrade_confidence(payload.confidence, "low")
        warnings.append(
            WarningItem(code="VERIFICATION_EMPTY", message="Verifier returned no checks.")
        )
        return
    
    failed_fields = [v for v in verifications if not v.verified]
    if len(failed_fields) >= 3:
        payload.confidence = downgrade_confidence(payload.confidence, "failed")
        warnings.append(
            WarningItem(
                code="VERIFICATION_FAILED",
                message="Multiple extracted fields could not be verified against JD text.",
                details={"failed_fields": [v.field for v in failed_fields]},
            )
        )
    elif len(failed_fields) >= 2:
        payload.confidence = downgrade_confidence(payload.confidence, "low")
        warnings.append(
            WarningItem(
                code="VERIFICATION_FAILED",
                message="Some extracted fields could not be verified against JD text.",
                details={"failed_fields": [v.field for v in failed_fields]},
            )
        )
    
    req_ver = next((v for v in verifications if v.field == "required_skills"), None)
    if req_ver and not req_ver.verified:
        payload.confidence = downgrade_confidence(payload.confidence, "low")


def _run_smart_retry(
    jd_text: str,
    payload: any,
    verifications: list,
    request_id: str,
    canonical_user_skills: List[str],
) -> Tuple[any, list, list, int]:
    """
    Run smart retry for failed verifications.
    
    Returns:
        (updated_payload, updated_verifications, new_warnings, retries_used)
    """
    from app.schemas import WarningItem
    
    RETRYABLE_FIELDS = {"role", "required_skills", "work_mode", "experience_required"}
    
    failed_fields = [v.field for v in verifications if not v.verified]
    retryable_failed = [f for f in failed_fields if f in RETRYABLE_FIELDS]
    
    if not (1 <= len(retryable_failed) <= 2):
        return payload, verifications, [], 0
    
    logger.info("pipeline_smart_retry request_id=%s fields=%s", request_id, retryable_failed)
    
    warnings = []
    retries_used = 0
    
    try:
        # Run correction
        corrections = run_correction(
            jd_text,
            payload.model_dump(),
            retryable_failed,
            request_id=request_id
        )
        
        # Merge safely (only overwrite failed fields)
        for field in retryable_failed:
            if field in corrections:
                setattr(payload, field, corrections[field])
        
        # Re-canonicalize skills if they were corrected
        if "required_skills" in retryable_failed:
            payload.required_skills = canonicalize_list(payload.required_skills or [])
        if "required_one_of" in retryable_failed:
            payload.required_one_of = canonicalize_pools(payload.required_one_of or [])
        
        # Remove from required_skills anything already in required_one_of
        if payload.required_one_of:
            one_of_flat = {s.lower() for pool in payload.required_one_of for s in pool}
            payload.required_skills = [
                s for s in (payload.required_skills or [])
                if s.lower() not in one_of_flat
            ]
        
        retries_used = 1
        warnings.append(
            WarningItem(
                code="RETRY_APPLIED",
                message=f"Corrected fields: {', '.join(retryable_failed)}"
            )
        )
        
        # Only re-run if at least one correction was non-null
        if not all(v is None for v in corrections.values()):
            payload = apply_stage2b_decisions(payload, canonical_user_skills, request_id=request_id)
            new_verifications = run_verifier(jd_text, payload.model_dump(), request_id=request_id)
            
            # Remove old verification warnings
            warnings = [w for w in warnings if w.code not in ("VERIFICATION_FAILED", "VERIFICATION_EMPTY")]
            _apply_verification_guardrails(payload, new_verifications, warnings)
            verifications = new_verifications
            
    except Exception as e:
        logger.error("pipeline_smart_retry_failed request_id=%s error=%s", request_id, str(e))
        warnings.append(
            WarningItem(
                code="RETRY_FAILED",
                message="Smart retry attempted but failed.",
                details={"error": str(e)}
            )
        )
    
    return payload, verifications, warnings, retries_used
