from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from app.schemas import JdAnalysisPayload
from core.skill_matching import run_skill_matching, MatchingResult, evaluate_pools
from core.skill_canonicalizer import get_skill_weight

logger = logging.getLogger("doc_intelligence.decision")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _clean_list(items: Iterable[str]) -> List[str]:
    """Clean and deduplicate skill list. Skills are already canonical."""
    out: List[str] = []
    seen: set = set()
    for x in items:
        if not x:
            continue
        x_clean = x.lower().strip()
        if not x_clean or x_clean in seen:
            continue
        seen.add(x_clean)
        out.append(x.strip())
    return out


def _parse_max_years(text: str | None) -> Optional[int]:
    """
    Extract years of experience from a JD string.
    Handles:  '2-4 years', '3+ years', 'minimum 2 years'.
    Returns MIN of numbers found — so '2-4' → 2 (conservative for eligibility).
    Returns None if no year-related number found.
    """
    if not text:
        return None
    # Match patterns like "2 years", "3+ yrs", "2-4 year", "5+ years"
    matches = re.findall(r"(\d+)\s*[\+\-]?\s*(?:year|yr|years|yrs)", text.lower())
    nums = [int(n) for n in matches]
    return min(nums) if nums else None


# ---------------------------------------------------------------------------
# Sub-components — each independently testable
# ---------------------------------------------------------------------------

@dataclass
class ExperienceGate:
    """
    Single responsibility: decide whether experience blocks this candidate.

    Real-world alignment:
    - Freshers (0 exp) should ONLY apply to roles requiring 0-1 years.
    - 3+ years = senior/lead = hard stop (changed from 4).
    - 2-3 years = mid-level = hard stop for freshers.
    - 1 year = borderline — flag as caution, not hard stop.
    """

    years_required: Optional[int]
    red_flags: List[str] = None

    SENIOR_THRESHOLD = 3  # Changed from 4 to 3
    EXPERIENCE_HARD_STOP = 2   # years: anything >= this is a hard stop for freshers
    EXPERIENCE_CAUTION = 1     # years: flag but don't hard-block

    def __post_init__(self):
        if self.red_flags is None:
            self.red_flags = []

    @property
    def is_hard_stop(self) -> bool:
        return self.years_required is not None and self.years_required >= self.EXPERIENCE_HARD_STOP

    @property
    def is_caution(self) -> bool:
        return (
            self.years_required is not None
            and self.EXPERIENCE_CAUTION <= self.years_required < self.EXPERIENCE_HARD_STOP
        )

    @property
    def is_senior(self) -> bool:
        # Gate 1: explicit years requirement
        years_senior = self.years_required is not None and self.years_required >= self.SENIOR_THRESHOLD
        if years_senior:
            return True

        # Gate 2: red flags that unambiguously mean "you must manage direct reports"
        # NOTE: "team handling", "people management" = red flags but NOT hard stops alone.
        # They increase red_flag_count → adjusted score penalty instead.
        # Only hard-stop on explicit organisational authority language.
        hard_authority_terms = ["direct reports", "hiring decisions", "performance reviews",
                                "manage a team of", "managing a team", "head of engineering"]
        for flag in (self.red_flags or []):
            flag_lower = flag.lower()
            if any(term in flag_lower for term in hard_authority_terms):
                return True

        return False

    def reason(self) -> str:
        if self.is_senior:
            years_str = f"{self.years_required}+ years" if self.years_required else "senior-level indicators"
            return f"Senior/lead role — requires {years_str} experience."
        if self.is_hard_stop:
            return f"Requires {self.years_required}+ years — not suitable for freshers."
        if self.is_caution:
            return f"Requires ~{self.years_required} year(s) experience — apply with caution."
        return ""


@dataclass
class ScoringContext:
    """Bundles all inputs needed for the final scoring decision."""

    # Flat skills
    flat_matched: List[str]
    flat_gaps: List[str]
    flat_required_total: int

    # Pool skills
    pool_results: List  # List[PoolResult] — satisfied flag per pool
    pool_matched_labels: List[str]
    pool_gap_labels: List[str]

    # Other context
    exp_gate: ExperienceGate
    red_flag_count: int
    entry_level_confirmed: bool
    retries_used: int

    # Thresholds
    RATIO_HARD_STOP: float = 0.65
    ABSOLUTE_HARD_STOP_WITH_HIGH_RATIO: int = 5
    ABSOLUTE_HARD_STOP_RATIO_FLOOR: float = 0.4
    CAUTION_RATIO: float = 0.35

    @property
    def total_required(self) -> int:
        return self.flat_required_total + len(self.pool_results)

    @property
    def total_matched(self) -> int:
        pools_satisfied = sum(1 for p in self.pool_results if p.satisfied)
        return len(self.flat_matched) + pools_satisfied

    @property
    def total_gaps(self) -> int:
        pools_unsatisfied = sum(1 for p in self.pool_results if not p.satisfied)
        return len(self.flat_gaps) + pools_unsatisfied

    @property
    def gap_ratio(self) -> float:
        return self.total_gaps / self.total_required if self.total_required else 0.0

    @property
    def score(self) -> int:
        """Calculate weighted score based on skill importance."""
        if not self.total_required:
            return 0

        # Numerator: weight of matched flat skills + weight of satisfied pools
        matched_weight = sum(get_skill_weight(s) for s in self.flat_matched)
        pools_matched_weight = sum(
            max([get_skill_weight(s) for s in pool.pool] + [1.0])
            for pool in self.pool_results if pool.satisfied
        )
        total_matched_weight = matched_weight + pools_matched_weight

        # Denominator: weight of ALL flat requirements (matched + gap) + weight of ALL pools
        # flat_matched + flat_gaps = all flat required skills (no double-count)
        flat_required_weight = sum(get_skill_weight(s) for s in self.flat_matched + self.flat_gaps)
        pools_total_weight = sum(
            max([get_skill_weight(s) for s in pool.pool] + [1.0])
            for pool in self.pool_results
        )
        total_required_weight = flat_required_weight + pools_total_weight

        if total_required_weight == 0:
            return 0

        return round(total_matched_weight * 100 / total_required_weight)

    @property
    def all_gaps(self) -> List[str]:
        return self.flat_gaps + self.pool_gap_labels

    @property
    def all_matched(self) -> List[str]:
        return self.flat_matched + self.pool_matched_labels

    @property
    def is_hard_stop(self) -> bool:
        # Entry-level small JDs get more lenient treatment
        if self.entry_level_confirmed and self.total_required <= 4:
            return self.gap_ratio > 0.80   # much more lenient for small entry-level JDs
        pure_ratio = self.gap_ratio > self.RATIO_HARD_STOP
        large_abs = (
            self.total_gaps > self.ABSOLUTE_HARD_STOP_WITH_HIGH_RATIO
            and self.gap_ratio > self.ABSOLUTE_HARD_STOP_RATIO_FLOOR
        )
        return pure_ratio or large_abs

    @property
    def is_caution(self) -> bool:
        return self.gap_ratio > self.CAUTION_RATIO

    def gap_summary(self, limit: int = 5) -> str:
        preview = self.all_gaps[:limit]
        suffix = "..." if len(self.all_gaps) > limit else ""
        return ", ".join(preview) + suffix

    def hard_stop_reason(self) -> str:
        return (
            f"Too many skill gaps — {self.total_gaps}/{self.total_required} requirements unmet "
            f"({round(self.gap_ratio * 100)}%). Missing: {self.gap_summary()}."
        )


@dataclass
class DecisionResult:
    recommendation: str           # apply_now | apply_with_caution | not_recommended | insufficient_data
    decision_reason: str
    confidence: str
    match_score: Optional[int] = None
    adjusted_score: Optional[int] = None


# ---------------------------------------------------------------------------
# Decision Engine
# ---------------------------------------------------------------------------

class DecisionEngine:
    """
    Deterministic decision engine for JD ↔ candidate matching.

    Design principles:
    ─────────────────
    1. ALL thresholds are class constants — one place to tune.
    2. No hardcoded skill/domain maps — they rot as JD vocabulary changes.
    3. Fail fast in a strict gate order: data → experience → skills → score.
    4. None-safety everywhere — Agent 1 extraction is imperfect.
    5. Confidence reflects PIPELINE quality, not just recommendation.
    6. Red flags deduct from adjusted_score but don't override hard gates.
    """

    # Score thresholds
    APPLY_NOW_THRESHOLD = 70      # was 80
    CAUTION_THRESHOLD = 35          # was 40

    # Red flag penalty per flag (capped)
    RED_FLAG_PENALTY_PER = 10
    RED_FLAG_PENALTY_CAP = 30

    def __init__(
        self,
        payload: JdAnalysisPayload,
        user_skills: List[str],
        request_id: str,
    ) -> None:
        self.payload = payload
        self.user_skills = _clean_list(user_skills)
        self.request_id = request_id

    # ── Public entry ──────────────────────────────────────────────────────

    def run(self) -> JdAnalysisPayload:
        p = self.payload
        req_skills = _clean_list(p.required_skills or [])

        # GATE 0: Data completeness — if extraction failed, stop here.
        has_pools = bool(p.required_one_of and any(pool for pool in p.required_one_of if pool))
        if not req_skills and not has_pools:
            return self._commit(
                DecisionResult(
                    recommendation="insufficient_data",
                    decision_reason=(
                        "Required skills list is empty — JD extraction may have failed. "
                        "Review source document or re-run extraction."
                    ),
                    confidence="failed",
                    match_score=None,
                    adjusted_score=None,
                )
            )

        # Skill matching - uses consolidated module (flat + pools in one call)
        match_result = run_skill_matching(
            req_skills,
            p.required_one_of or [],
            self.user_skills,
            request_id=self.request_id
        )

        logger.info(
            "[MATCH_DEBUG] request_id=%s flat_matched=%d flat_gaps=%d "
            "pools=%d pools_satisfied=%d",
            self.request_id,
            len(match_result.matched_skills),
            len(match_result.gap_skills),
            len(match_result.pool_results),
            sum(1 for p in match_result.pool_results if p.satisfied)
        )

        ctx = ScoringContext(
            flat_matched=match_result.matched_skills,
            flat_gaps=match_result.gap_skills,
            flat_required_total=len(req_skills),
            pool_results=match_result.pool_results,
            pool_matched_labels=match_result.pool_matched,
            pool_gap_labels=match_result.pool_gaps,
            exp_gate=ExperienceGate(
                years_required=_parse_max_years(p.experience_required),
                red_flags=p.red_flags or []
            ),
            red_flag_count=len(p.red_flags) if p.red_flags else 0,
            entry_level_confirmed=p.truly_entry_level is True,
            retries_used=p.retries_used or 0,
        )

        logger.info(
            "[CTX_DEBUG] request_id=%s all_matched=%s all_gaps=%s",
            self.request_id,
            ctx.all_matched,
            ctx.all_gaps,
        )

        p.matched_skills = ctx.all_matched
        p.skill_gaps = ctx.all_gaps

        logger.debug(
            "[%s] score=%d gap_ratio=%.2f exp_years=%s entry_level=%s red_flags=%d retries=%d",
            self.request_id,
            ctx.score,
            ctx.gap_ratio,
            ctx.exp_gate.years_required,
            ctx.entry_level_confirmed,
            ctx.red_flag_count,
            ctx.retries_used,
        )

        result = self._decide(ctx)
        return self._commit(result)

    # ── Core decision logic ───────────────────────────────────────────────

    def _decide(self, ctx: ScoringContext) -> DecisionResult:

        # GATE 1: Experience — most disqualifying, checked first.
        # Order: senior (stricter) → any experience required (less strict).
        if ctx.exp_gate.is_senior:
            return DecisionResult(
                recommendation="not_recommended",
                decision_reason=ctx.exp_gate.reason(),
                confidence="high",
                match_score=ctx.score,
                adjusted_score=None,
            )

        if ctx.exp_gate.is_hard_stop:
            return DecisionResult(
                recommendation="not_recommended",
                decision_reason=ctx.exp_gate.reason(),
                confidence="high",
                match_score=ctx.score,
                adjusted_score=None,
            )

        # GATE 2: Skill viability — too many gaps = caution (not hard stop)
        if ctx.is_hard_stop:
            return DecisionResult(
                recommendation="apply_with_caution",
                decision_reason=ctx.hard_stop_reason(),
                confidence="medium",
                match_score=ctx.score,
                adjusted_score=None,
            )

        # Adjusted score — red flags reduce the effective score.
        penalty = min(ctx.red_flag_count * self.RED_FLAG_PENALTY_PER, self.RED_FLAG_PENALTY_CAP)
        adjusted = max(ctx.score - penalty, 0)

        # GATE 3: Score-based recommendation.
        return self._score_decision(ctx, raw_score=ctx.score, adjusted=adjusted)

    def _score_decision(
        self,
        ctx: ScoringContext,
        raw_score: int,
        adjusted: int,
    ) -> DecisionResult:
        confidence = self._compute_confidence(ctx)

        # ── apply_now (strictest conditions) ──────────────────────────────
        if (
            adjusted >= self.APPLY_NOW_THRESHOLD
            and ctx.red_flag_count == 0
        ):
            return DecisionResult(
                recommendation="apply_now",
                decision_reason=(
                    f"Strong match ({raw_score}%), no red flags."
                ),
                confidence=confidence,
                match_score=raw_score,
                adjusted_score=adjusted,
            )

        # ── apply_with_caution ─────────────────────────────────────────────
        if adjusted >= self.CAUTION_THRESHOLD:
            reasons: List[str] = []

            if not ctx.entry_level_confirmed:
                reasons.append("entry-level status not confirmed by JD")
            if ctx.red_flag_count:
                reasons.append(f"{ctx.red_flag_count} red flag(s) detected (−{min(ctx.red_flag_count * self.RED_FLAG_PENALTY_PER, self.RED_FLAG_PENALTY_CAP)} pts)")
            if ctx.is_caution:
                reasons.append(f"skill gaps: {ctx.gap_summary()}")
            if ctx.exp_gate.is_caution:
                reasons.append(ctx.exp_gate.reason())

            reason_str = "; ".join(reasons) if reasons else "acceptable match with minor concerns."

            return DecisionResult(
                recommendation="apply_with_caution",
                decision_reason=f"Partial match ({raw_score}% raw, {adjusted}% adjusted). {reason_str}.",
                confidence=confidence,
                match_score=raw_score,
                adjusted_score=adjusted,
            )

        # ── not_recommended (failed score threshold) ───────────────────────
        return DecisionResult(
            recommendation="not_recommended",
            decision_reason=(
                f"Insufficient match ({raw_score}%, {adjusted}% adjusted). "
                f"Missing: {ctx.gap_summary()}."
            ),
            confidence=confidence,
            match_score=raw_score,
            adjusted_score=adjusted,
        )

    # ── Confidence ────────────────────────────────────────────────────────

    def _compute_confidence(self, ctx: ScoringContext) -> str:
        """
        Confidence = how much we trust the pipeline output, not the recommendation.

        Rules:
        - failed:  extraction produced no usable data
        - high:    first-pass clean result, full or near-full match
        - medium:  one retry OR minor gaps, but result is still trustworthy
        - low:     multiple retries, high gap ratio, or very few required skills
        """
        # Sparse JD penalty — if total requirements is very small AND there are gaps, we can't
        # trust the score regardless of how well the candidate matched.
        # A JD with 1-2 requirements is almost certainly missing implicit expectations.
        if ctx.total_required <= 2 and ctx.gap_ratio > 0:
            return "low"

        if ctx.retries_used == 0 and ctx.gap_ratio == 0:
            return "high"
        if ctx.retries_used <= 1 and ctx.gap_ratio <= 0.25:
            return "medium"
        return "low"

    # ── Commit to payload ─────────────────────────────────────────────────

    def _commit(self, result: DecisionResult) -> JdAnalysisPayload:
        p = self.payload
        p.recommendation = result.recommendation
        p.decision_reason = result.decision_reason
        p.confidence = result.confidence
        p.match_score = result.match_score
        if hasattr(p, "adjusted_score"):
            p.adjusted_score = result.adjusted_score
        return p


# ---------------------------------------------------------------------------
# Public entry point — drop-in replacement for apply_stage2b_decisions
# ---------------------------------------------------------------------------

def apply_stage2b_decisions(
    payload: JdAnalysisPayload,
    user_skills: List[str],
    *,
    request_id: str,
) -> JdAnalysisPayload:
    """
    Drop-in replacement.
    Internally delegates to DecisionEngine so each component is unit-testable.
    """
    return DecisionEngine(payload, user_skills, request_id=request_id).run()

