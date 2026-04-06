"""Skill canonicalization layer.

Converts raw extracted skills to canonical form using normalise.json.
Logs unknown skills for later addition to skills_master.json.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("doc_intelligence.canonicalizer")

# Cache for normalize mapping
_normalize_cache: Optional[Dict[str, str]] = None

# Cache for skills_master metadata (weights, categories)
_skills_master_meta_cache: Optional[Dict[str, Any]] = None


def _load_normalize_map() -> Dict[str, str]:
    """Build normalize map dynamically from skills_master.json."""
    global _normalize_cache
    
    if _normalize_cache is not None:
        return _normalize_cache
    
    core_dir = os.path.dirname(os.path.abspath(__file__))
    master_path = os.path.join(core_dir, "skills_master.json")
    
    normalize_map: Dict[str, str] = {}
    
    try:
        with open(master_path, "r", encoding="utf-8") as f:
            skills_master = json.load(f)
        
        # Build map from canonical name and all aliases
        for canonical, skill_def in skills_master.items():
            # Add canonical name
            norm_canonical = _normalize_text(canonical)
            if norm_canonical:
                normalize_map[norm_canonical] = canonical
            
            # Add all aliases
            for alias in skill_def.get("aliases", []):
                norm_alias = _normalize_text(alias)
                if norm_alias and norm_alias not in normalize_map:
                    normalize_map[norm_alias] = canonical
        
        # Ensure C++ without slash also maps correctly
        if "c/c++" in normalize_map:
            normalize_map["c++"] = normalize_map["c/c++"]
            normalize_map["cpp"] = normalize_map["c/c++"]
        
        _normalize_cache = normalize_map
        logger.info("canonicalizer_built_from_master canonical=%d mappings=%d", 
                   len(skills_master), len(normalize_map))
        return _normalize_cache
        
    except FileNotFoundError:
        logger.error("canonicalizer_skills_master_not_found path=%s", master_path)
        _normalize_cache = {}
        return _normalize_cache
    except json.JSONDecodeError as e:
        logger.error("canonicalizer_skills_master_invalid error=%s", e)
        _normalize_cache = {}
        return _normalize_cache


def _normalize_text(s: str) -> str:
    """
    Normalize text for matching:
    - lowercase
    - strip whitespace
    - remove dots (react.js → reactjs)
    - replace hyphens with spaces (scikit-learn → scikit learn)
    - collapse multiple spaces
    """
    if not s:
        return ""
    s = s.lower().strip()
    s = s.replace(".", "")  # react.js → reactjs
    s = s.replace("-", " ")  # scikit-learn → scikit learn
    s = " ".join(s.split())  # collapse multiple spaces
    return s


def _log_unknown_skill(skill: str) -> None:
    """Log unknown skill for later addition to skills_master.json."""
    # Create logs directory if needed
    logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    os.makedirs(logs_dir, exist_ok=True)
    
    log_path = os.path.join(logs_dir, "unmapped_skills.txt")
    
    # Read existing skills to avoid duplicates
    existing: Set[str] = set()
    if os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                existing = {line.strip() for line in f if line.strip()}
        except Exception:
            pass
    
    # Append if new
    if skill not in existing:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{skill}\n")
            logger.debug("canonicalizer_logged_unknown skill='%s'", skill)
        except Exception as e:
            logger.warning("canonicalizer_log_failed skill='%s' error=%s", skill, e)


def canonicalize_skill(raw_skill: str) -> str:
    """
    Convert a raw skill to its canonical form.
    If not in mapping, return the normalized raw skill (don't drop it).
    
    Args:
        raw_skill: Raw skill string from extractor (e.g., "React.js")
    
    Returns:
        Canonical skill name or normalized raw skill if not found
    """
    if not raw_skill or not isinstance(raw_skill, str):
        return ""
    
    normalize_map = _load_normalize_map()
    normalized = _normalize_text(raw_skill)
    
    # Try direct lookup
    if normalized in normalize_map:
        canonical = normalize_map[normalized]
        logger.debug("canonicalizer_matched raw='%s' → canonical='%s'", raw_skill, canonical)
        return canonical
    
    # Not found - return normalized raw skill (don't drop it)
    _log_unknown_skill(raw_skill)
    logger.debug("canonicalizer_unknown_pass_through raw='%s' → normalized='%s'", raw_skill, normalized)
    return normalized or raw_skill.strip()


def canonicalize_list(raw_skills: List[str]) -> List[str]:
    """
    Canonicalize a list of raw skills.
    
    Args:
        raw_skills: List of raw skill strings
    
    Returns:
        List of canonical skill names (unknown skills passed through as normalized)
    """
    if not raw_skills:
        return []
    
    seen: Set[str] = set()
    canonical_list: List[str] = []
    
    for raw in raw_skills:
        canonical = canonicalize_skill(raw)
        # Filter out empty strings but keep unknowns (they're normalized)
        if canonical and canonical not in seen:
            seen.add(canonical)
            canonical_list.append(canonical)
    
    logger.info(
        "canonicalize_list input=%d output=%d",
        len(raw_skills),
        len(canonical_list)
    )
    
    return canonical_list


def canonicalize_pools(raw_pools: List[List[str]]) -> List[List[str]]:
    """
    Canonicalize skill pools.
    
    Args:
        raw_pools: List of skill pools (each pool is a list of alternatives)
    
    Returns:
        List of canonical pools (empty pools removed)
    """
    if not raw_pools:
        return []
    
    canonical_pools: List[List[str]] = []
    
    for pool in raw_pools:
        if not pool:
            continue
        
        canonical_pool = canonicalize_list(pool)
        if canonical_pool:  # Only keep pools with at least one valid skill
            # Remove duplicates within pool while preserving order
            seen: Set[str] = set()
            deduped: List[str] = []
            for skill in canonical_pool:
                if skill not in seen:
                    seen.add(skill)
                    deduped.append(skill)
            canonical_pools.append(deduped)
    
    logger.info(
        "canonicalize_pools input=%d output=%d",
        len(raw_pools),
        len(canonical_pools)
    )
    
    return canonical_pools


def _load_skills_master_direct() -> Dict[str, Any]:
    """Load skills_master.json using a module-level cache."""
    global _skills_master_meta_cache
    if _skills_master_meta_cache is not None:
        return _skills_master_meta_cache

    core_dir = os.path.dirname(os.path.abspath(__file__))
    master_path = os.path.join(core_dir, "skills_master.json")

    try:
        with open(master_path, "r", encoding="utf-8") as f:
            _skills_master_meta_cache = json.load(f)
        return _skills_master_meta_cache
    except Exception:
        _skills_master_meta_cache = {}
        return _skills_master_meta_cache


def get_skill_weight(skill: str) -> float:
    """
    Get the importance weight for a canonical skill.
    
    Args:
        skill: Canonical skill name
    
    Returns:
        Weight value (default 1.0 if not found)
    """
    master = _load_skills_master_direct()
    skill_lower = skill.lower()
    for canonical, data in master.items():
        if canonical.lower() == skill_lower:
            return data.get("weight", 1.0)
    return 1.0


def get_skill_category(skill: str) -> Optional[str]:
    """
    Get the category for a canonical skill.
    
    Args:
        skill: Canonical skill name
    
    Returns:
        Category string or None
    """
    master = _load_skills_master_direct()
    skill_lower = skill.lower()
    for canonical, data in master.items():
        if canonical.lower() == skill_lower:
            return data.get("category")
    return None
