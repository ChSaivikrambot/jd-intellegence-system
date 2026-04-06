"""Consolidated skill matching module.

Uses skills_master.json as the single source of truth.
All inputs are expected to be already canonicalized.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger("doc_intelligence.skill_matching")


# ---------------------------------------------------------------------------
# Load skills_master.json as brain
# ---------------------------------------------------------------------------

_skills_master_cache: Optional[Dict[str, Dict]] = None


def _load_skills_master() -> Dict[str, Dict]:
    """Load skills_master.json as the single source of truth."""
    global _skills_master_cache
    
    if _skills_master_cache is not None:
        return _skills_master_cache
    
    core_dir = os.path.dirname(os.path.abspath(__file__))
    master_path = os.path.join(core_dir, "skills_master.json")
    
    try:
        with open(master_path, "r", encoding="utf-8") as f:
            _skills_master_cache = json.load(f)
        logger.info("matcher_loaded_skills_master count=%d", len(_skills_master_cache))
        return _skills_master_cache
    except FileNotFoundError:
        logger.error("matcher_skills_master_not_found path=%s", master_path)
        _skills_master_cache = {}
        return _skills_master_cache
    except json.JSONDecodeError as e:
        logger.error("matcher_skills_master_invalid error=%s", e)
        _skills_master_cache = {}
        return _skills_master_cache


def get_skill_definition(skill: str) -> Optional[Dict]:
    """Get skill definition from skills_master."""
    master = _load_skills_master()
    # Try exact match first
    if skill in master:
        return master[skill]
    # Try lowercase match
    skill_lower = skill.lower()
    for canonical, definition in master.items():
        if canonical.lower() == skill_lower:
            return definition
        # Check aliases
        for alias in definition.get("aliases", []):
            if alias.lower() == skill_lower:
                return definition
    return None


# ---------------------------------------------------------------------------
# Pool Result Types
# ---------------------------------------------------------------------------

@dataclass
class PoolResult:
    """Result of evaluating a skill pool."""
    pool: List[str]           # The pool skills (canonical)
    satisfied: bool           # Did candidate have at least one?
    matched_by: str | None    # Which skill matched (if any)


@dataclass
class MatchingResult:
    """Complete matching result for a candidate."""
    # Flat skills
    matched_skills: List[str]
    gap_skills: List[str]
    
    # Pool results
    pool_results: List[PoolResult]
    pool_matched: List[str]   # Skills from satisfied pools
    pool_gaps: List[str]      # All skills from unsatisfied pools
    
    @property
    def all_matched(self) -> List[str]:
        return self.matched_skills + self.pool_matched
    
    @property
    def all_gaps(self) -> List[str]:
        return self.gap_skills + self.pool_gaps
    
    @property
    def match_count(self) -> int:
        pools_satisfied = sum(1 for p in self.pool_results if p.satisfied)
        return len(self.matched_skills) + pools_satisfied
    
    @property
    def requirement_count(self) -> int:
        return len(self.matched_skills) + len(self.gap_skills) + len(self.pool_results)


# ---------------------------------------------------------------------------
# Skill Matching Logic (clean, using skills_master)
# ---------------------------------------------------------------------------

def _is_match(jd_skill: str, candidate_set: Set[str]) -> bool:
    """
    Check if JD skill matches any candidate skill.
    
    Logic:
    1. Direct match (exact or alias - handled by canonicalization)
    2. For hierarchical skills, check children if explicitly defined
    
    Args:
        jd_skill: Canonical JD skill
        candidate_set: Set of canonical candidate skills (lowercase)
    
    Returns:
        True if match found
    """
    skill_lower = jd_skill.lower().strip()
    
    # 1. Direct match (already canonicalized)
    if skill_lower in candidate_set:
        return True
    
    # 2. Check skills_master for match_type
    skill_def = get_skill_definition(jd_skill)
    
    if skill_def:
        match_type = skill_def.get("match_type", "strict")
        
        # strict: only exact match (already checked above)
        if match_type == "strict":
            return False
        
        # alias: handled by canonicalization, no additional matching needed
        if match_type == "alias":
            return False
        
        # hierarchical: check children
        if match_type == "hierarchical":
            children = skill_def.get("children", [])
            for child in children:
                if child.lower().strip() in candidate_set:
                    logger.debug("skill_matched_hierarchical parent='%s' child='%s'", 
                               jd_skill, child)
                    return True
    
    return False


def match_flat_skills(
    jd_skills: List[str],
    candidate_skills: List[str],
) -> Tuple[List[str], List[str]]:
    """
    Match flat (required) skills against candidate skills.
    
    Args:
        jd_skills: Canonical JD skills (already normalized)
        candidate_skills: Canonical candidate skills (already normalized)
    
    Returns:
        (matched_list, gaps_list)
    """
    if not jd_skills:
        return [], []
    
    # Build candidate skill set for O(1) lookup
    candidate_set: Set[str] = {s.lower().strip() for s in candidate_skills if s}
    
    matched: List[str] = []
    gaps: List[str] = []
    
    for skill in jd_skills:
        if not skill:
            continue
        
        skill_lower = skill.lower().strip()
        
        # Use clean matching logic from skills_master
        if _is_match(skill, candidate_set):
            matched.append(skill)
            logger.debug("skill_matched skill='%s'", skill)
        else:
            gaps.append(skill)
    
    logger.info(
        "match_flat_skills jd=%d candidate=%d matched=%d gaps=%d",
        len(jd_skills), len(candidate_skills), len(matched), len(gaps)
    )
    
    return matched, gaps


# ---------------------------------------------------------------------------
# Pool Evaluation
# ---------------------------------------------------------------------------

def evaluate_pools(
    required_pools: List[List[str]],
    candidate_skills: List[str],
) -> Tuple[List[PoolResult], List[str], List[str]]:
    """
    Evaluate skill pools against candidate skills.
    
    Args:
        required_pools: List of skill pools (each pool is list of alternatives)
        candidate_skills: Canonical candidate skills
    
    Returns:
        (pool_results, pool_matched_labels, pool_gap_labels)
    """
    if not required_pools:
        return [], [], []
    
    # Build candidate skill set
    candidate_set: Set[str] = {s.lower().strip() for s in candidate_skills if s}
    
    results: List[PoolResult] = []
    pool_matched: List[str] = []
    pool_gaps: List[str] = []
    seen_pool_matched: Set[str] = set()   # prevent cross-pool duplicates

    for pool in required_pools:
        if not pool:
            continue

        hits: List[str] = []
        for skill in pool:
            if not skill:
                continue
            if _is_match(skill, candidate_set):
                hits.append(skill)

        if hits:
            results.append(PoolResult(pool=pool, satisfied=True, matched_by=hits[0]))
            for h in hits:
                h_lower = h.lower()
                if h_lower not in seen_pool_matched:
                    seen_pool_matched.add(h_lower)
                    pool_matched.append(h)
        else:
            results.append(PoolResult(pool=pool, satisfied=False, matched_by=None))
            pool_gaps.extend(s for s in pool if s)
    
    logger.debug(
        "evaluate_pools pools=%d satisfied=%d",
        len(required_pools), len([r for r in results if r.satisfied])
    )
    
    return results, pool_matched, pool_gaps


# ---------------------------------------------------------------------------
# Unified Entry Point
# ---------------------------------------------------------------------------

def run_skill_matching(
    required_skills: List[str],
    required_pools: List[List[str]],
    candidate_skills: List[str],
    *,
    request_id: str = "",
) -> MatchingResult:
    """
    Run complete skill matching (flat + pools).
    
    Args:
        required_skills: Flat required skills (canonical)
        required_pools: Skill pools (canonical)
        candidate_skills: Candidate skills (canonical)
        request_id: For logging
    
    Returns:
        MatchingResult with all matching data
    """
    # Match flat skills
    matched, gaps = match_flat_skills(required_skills, candidate_skills)
    
    # Evaluate pools
    pool_results, pool_matched, pool_gaps = evaluate_pools(
        required_pools, candidate_skills
    )
    
    result = MatchingResult(
        matched_skills=matched,
        gap_skills=gaps,
        pool_results=pool_results,
        pool_matched=pool_matched,
        pool_gaps=pool_gaps,
    )
    
    logger.info(
        "skill_matching_complete request_id=%s "
        "flat_matched=%d flat_gaps=%d pools=%d pools_satisfied=%d",
        request_id,
        len(matched), len(gaps),
        len(pool_results),
        len([p for p in pool_results if p.satisfied])
    )
    
    return result


# ---------------------------------------------------------------------------
# Backward Compatibility
# ---------------------------------------------------------------------------

def run_skill_matcher(
    jd_skills: List[str],
    user_skills: List[str],
    *,
    request_id: str = "",
) -> Tuple[List[str], List[str]]:
    """
    Backward-compatible wrapper for flat skill matching only.
    
    Returns (matched, gaps) for flat skills.
    """
    matched, gaps = match_flat_skills(jd_skills, user_skills)
    return matched, gaps
