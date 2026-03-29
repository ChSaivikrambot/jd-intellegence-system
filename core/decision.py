from __future__ import annotations

import re
from typing import Iterable, List

from app.schemas import JdAnalysisPayload


def _canon(s: str) -> str:
    return " ".join(s.strip().lower().split())


def _clean_list(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for x in items:
        if not x:
            continue
        cx = _canon(x)
        if not cx or cx in seen:
            continue
        seen.add(cx)
        out.append(x.strip())
    return out


def _is_overqualified_role(experience_required: str | None) -> bool:
    if not experience_required:
        return False
    s = experience_required.lower()
    if "senior" in s:
        return True
    # Minimal numeric signal: 5+ years or more.
    nums = [int(n) for n in re.findall(r"\d+", s)]
    return any(n >= 5 for n in nums)


def apply_stage2b_decisions(payload: JdAnalysisPayload, user_skills: List[str]) -> JdAnalysisPayload:
    """
    Stage 2B (deterministic): matched skills, gaps, score, recommendation.
    """
    req_skills = _clean_list(payload.required_skills)
    user = _clean_list(user_skills)

    user_set = {_canon(s) for s in user}

    matched = [s for s in req_skills if _canon(s) in user_set]
    gaps = [s for s in req_skills if _canon(s) not in user_set]

    payload.matched_skills = matched
    payload.skill_gaps = gaps

    if req_skills:
        payload.match_score = round(len(matched) * 100 / len(req_skills))
    else:
        payload.match_score = None

    # Deterministic recommendation rules with strict priority order.
    red_flags_present = bool(payload.red_flags)
    entry_level = payload.truly_entry_level
    score = payload.match_score
    is_overqualified_role = _is_overqualified_role(payload.experience_required)

    if (entry_level is False and red_flags_present) or is_overqualified_role:
        payload.recommendation = "high_risk"
        if is_overqualified_role:
            payload.decision_reason = "Role requires senior experience (5+ years or senior profile). Not suitable for entry-level candidates."
        else:
            payload.decision_reason = "Non-entry-level role with red flags present."
    elif score is None:
        payload.recommendation = "insufficient_data"
        payload.decision_reason = "Missing required_skills, cannot compute match score."
    elif score < 60:
        payload.recommendation = "upskill_first"
        payload.decision_reason = (
            f"Matched {len(matched)}/{len(req_skills)} required skills. "
            f"Missing: {', '.join(gaps) if gaps else 'None'}."
        )
    elif red_flags_present:
        payload.recommendation = "apply_with_caution"
        payload.decision_reason = "Red flags present even though match score is acceptable."
    elif score >= 80 and entry_level is True:
        payload.recommendation = "apply_now"
        payload.decision_reason = "High match score, no red flags, and role appears entry-level."
    else:
        payload.recommendation = "apply_with_caution"
        payload.decision_reason = "Default caution path for mixed signals."

    # Stage 2B.1: deterministic confidence (no verifier yet).
    # Blend data quality with retries so weak JDs do not get false "high".
    if payload.recommendation == "insufficient_data":
        payload.confidence = "failed"
    elif not req_skills or len(req_skills) < 2:
        payload.confidence = "low"
    elif payload.retries_used <= 0:
        payload.confidence = "high"
    elif payload.retries_used == 1:
        payload.confidence = "medium"
    else:
        payload.confidence = "low"

    return payload

