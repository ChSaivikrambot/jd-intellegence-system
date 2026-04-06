from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field
from core.models.verifier import FieldVerification


class ErrorItem(BaseModel):
    code: str
    message: str
    retryable: bool = False
    details: Optional[Dict[str, Any]] = None


class WarningItem(BaseModel):
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None


class AnalyzeRequest(BaseModel):
    jd_text: str = Field(min_length=1, description="Raw job description text.")
    skills: List[str] = Field(default_factory=list, description="User skills list (optional for Stage 1).")


class JdAnalysisPayload(BaseModel):
    # Keep fields optional early (LLM later; Stage 1 is deterministic/dummy).
    company: Optional[str] = None
    role: Optional[str] = None
    truly_entry_level: Optional[bool] = None
    
    # NEW: Unified skills list (extractor output) — parser will classify into required/preferred
    skills: List[str] = Field(default_factory=list, description="All technical skills mentioned in JD (extractor output)")
    
    # Parser-classified fields
    required_skills: List[str] = Field(default_factory=list)
    required_one_of: List[List[str]] = Field(default_factory=list)  # Skill pools: need 1 from each
    preferred_skills: List[str] = Field(default_factory=list)
    experience_required: Optional[str] = None
    red_flags: List[str] = Field(default_factory=list)
    compensation: Optional[str] = None
    work_mode: Optional[Literal["remote", "hybrid", "onsite", "not_mentioned"]] = None

    # Where did the skills come from? (manual vs resume vs llm_extracted)
    skills_source: Literal["manual", "resume", "llm_extracted"] = "manual"

    # Decision layer fields (Stage 2B later). Included for shape stability.
    match_score: Optional[int] = Field(default=None, ge=0, le=100)
    adjusted_score: Optional[int] = Field(default=None, ge=0, le=100)
    matched_skills: List[str] = Field(default_factory=list)
    skill_gaps: List[str] = Field(default_factory=list)
    recommendation: Optional[
        Literal["apply_now", "apply_with_caution", "not_recommended", "insufficient_data"]
    ] = None
    decision_reason: Optional[str] = None
    confidence: Optional[Literal["high", "medium", "low", "failed"]] = None
    retries_used: int = Field(default=0, ge=0)
    verification: List[FieldVerification] = Field(default_factory=list)


class AnalyzeResponse(BaseModel):
    request_id: str
    status: Literal["ok", "error"]
    errors: List[ErrorItem] = Field(default_factory=list)
    warnings: List[WarningItem] = Field(default_factory=list)
    payload: Optional[JdAnalysisPayload] = None

