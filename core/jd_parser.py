"""
Rule-based JD parser.

AUTHORITY TABLE — these fields are owned by this module, not the LLM:
    work_mode          → keyword match
    truly_entry_level  → keyword match
    experience_required → regex
    red_flags          → phrase match
    required_one_of    → syntax patterns + skills_master validation
    required_skills    → classify_skills() (section-aware, word-boundary)
    preferred_skills   → classify_skills() (section-aware, word-boundary)

Uses skill_canonicalizer's existing normalize_map cache for pool validation.
Does NOT load skills_master.json itself. Does NOT call any LLM.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from core.jd_parser_patterns import (
    REMOTE_PHRASES, HYBRID_PHRASES, ONSITE_PHRASES,
    ENTRY_LEVEL_PHRASES,
    EXPERIENCE_REGEXES,
    RED_FLAG_LEADERSHIP, RED_FLAG_DEGREE, RED_FLAG_SENIORITY,
    POOL_TRIGGER_PHRASES,
    # NEW — imported below; defined in jd_parser_patterns.py
    REQUIRED_SECTION_HEADERS,
    PREFERRED_SECTION_HEADERS,
    REQUIRED_INLINE_SIGNALS,
    PREFERRED_INLINE_SIGNALS,
    EXAMPLE_PHRASES,
)
from core.skill_canonicalizer import _load_normalize_map, _normalize_text

logger = logging.getLogger("doc_intelligence.jd_parser")


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class ParsedJD:
    """Authoritative rule-based fields extracted from JD text."""
    work_mode: str = "not_mentioned"
    truly_entry_level: bool = False
    experience_required: Optional[str] = None
    red_flags: List[str] = field(default_factory=list)
    required_one_of: List[List[str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize(token: str) -> str:
    """Normalize a token for skills_master lookup."""
    return _normalize_text(token)


def _is_known_skill(token: str, normalize_map: Dict[str, str]) -> bool:
    """Return True if token maps to a known skill in skills_master."""
    return _normalize(token) in normalize_map


def _to_canonical(token: str, normalize_map: Dict[str, str]) -> Optional[str]:
    """Return canonical skill name for token, or None if unknown."""
    return normalize_map.get(_normalize(token))


def _split_to_tokens(fragment: str) -> List[str]:
    """
    Split a text fragment into candidate skill tokens.
    Handles: comma, 'or', 'and', slash as separators.
    Preserves skill-name characters: +, #, /, ., -
    """
    fragment = re.sub(r'\bor\b|\band\b', ',', fragment, flags=re.IGNORECASE)
    raw_tokens = fragment.split(',')
    cleaned = []
    for t in raw_tokens:
        t = t.strip().strip('()[]{}; ')
        if t and len(t) >= 1:
            cleaned.append(t)
    return cleaned


def _skills_from_tokens(
    tokens: List[str],
    normalize_map: Dict[str, str],
) -> List[str]:
    """
    Filter token list to only known canonical skills, preserving order.
    Deduplicates by canonical name.
    """
    seen: set = set()
    result: List[str] = []
    for t in tokens:
        canonical = _to_canonical(t.strip(), normalize_map)
        if canonical and canonical not in seen:
            seen.add(canonical)
            result.append(canonical)
    return result


def _clip_to_sentence(text: str) -> str:
    """Clip text at the first sentence/line boundary."""
    return re.split(r'[.\n;]', text)[0]


def _word_boundary_search(skill_lower: str, text: str) -> bool:
    """
    Check if skill appears in text with word boundaries.
    Handles skills with special chars: c++, node.js, ci/cd, react.js, etc.
    Uses regex word boundary with special-char awareness.
    """
    # Escape special regex chars in skill name
    escaped = re.escape(skill_lower)
    # Word boundary: preceded/followed by non-alphanumeric (space, comma, newline, etc.)
    # but NOT alphanumeric — avoids "go" matching inside "django" or "git" inside "agility"
    pattern = r'(?<![a-z0-9])' + escaped + r'(?![a-z0-9])'
    return bool(re.search(pattern, text, re.IGNORECASE))


# ---------------------------------------------------------------------------
# 1. Work mode  (authoritative)
# ---------------------------------------------------------------------------

def detect_work_mode(jd_lower: str) -> str:
    """
    Returns 'remote' | 'hybrid' | 'onsite' | 'not_mentioned'.
    Checks remote first — some JDs say "remote or hybrid"; remote wins.
    """
    for phrase in REMOTE_PHRASES:
        if phrase in jd_lower:
            return "remote"
    for phrase in HYBRID_PHRASES:
        if phrase in jd_lower:
            return "hybrid"
    for phrase in ONSITE_PHRASES:
        if phrase in jd_lower:
            return "onsite"
    return "not_mentioned"


# ---------------------------------------------------------------------------
# 2. Entry level  (authoritative)
# ---------------------------------------------------------------------------

def detect_entry_level(jd_lower: str) -> bool:
    """
    Returns True if JD is clearly entry-level / internship / fresher.
    Conservative — returns False when in doubt.
    """
    for phrase in ENTRY_LEVEL_PHRASES:
        if phrase in jd_lower:
            return True
    return False


# ---------------------------------------------------------------------------
# 3. Experience required  (authoritative)
# ---------------------------------------------------------------------------

def detect_experience(jd_text: str) -> Optional[str]:
    """
    Extract experience requirement string from original JD text.
    Returns the matched span as-is (preserves original casing and phrasing).
    Uses original text (not lowercased) so output is human-readable.
    Returns None if no year-based experience requirement found.
    """
    for pattern in EXPERIENCE_REGEXES:
        m = pattern.search(jd_text)
        if m:
            return m.group(0).strip()
    return None


# ---------------------------------------------------------------------------
# 4. Red flags  (authoritative)
# ---------------------------------------------------------------------------

def detect_red_flags(jd_lower: str, jd_text: str) -> List[str]:
    """
    Extract red flag phrases — signals that this role is not suitable
    for a fresher. Returns list of matched phrases (deduped).
    """
    flags: List[str] = []
    seen: set = set()

    def add(phrase: str) -> None:
        if phrase not in seen:
            seen.add(phrase)
            flags.append(phrase)

    for phrase in RED_FLAG_LEADERSHIP:
        if phrase in jd_lower:
            add(phrase)

    for phrase in RED_FLAG_DEGREE:
        if phrase in jd_lower:
            add(phrase)

    for phrase in RED_FLAG_SENIORITY:
        if phrase in jd_lower:
            add(phrase)

    return flags


# ---------------------------------------------------------------------------
# 5. Pool detection  (authoritative)
# ---------------------------------------------------------------------------

def _classify_pool_candidate(candidate: Dict) -> bool:
    """
    Classify a pool candidate as valid or not based on context.
    Returns True if pool should be added, False otherwise.
    """
    source = candidate["source"]
    context = candidate["context"]
    context_lower = context.lower()

    # RULE 1 — strong signals (always accept)
    if source in ("trigger", "or"):
        return True

    # RULE 1.5 — reject pools where all skills share a non-language, non-cloud category
    if source == "paren":
        from core.skill_canonicalizer import get_skill_category
        categories = {get_skill_category(s) for s in candidate["skills"] if get_skill_category(s)}
        hardware_categories = {"platform"}
        if categories and categories.issubset(hardware_categories):
            if "one or more" not in context and " or " not in context:
                return False

    # RULE 2 — reject if preferred context
    if any(word in context_lower for word in ["preferred", "nice to have", "plus"]):
        return False

    # RULE 3 — parenthesis needs support
    if source == "paren":
        if " or " in context_lower:
            return True
        if any(word in context_lower for word in ["experience with", "proficiency in", "knowledge of"]):
            return True
        return False

    return False


def _try_pool(
    fragment: str,
    normalize_map: Dict[str, str],
    min_skills: int = 2,
) -> Optional[List[str]]:
    """
    Given a text fragment that follows a pool-trigger phrase,
    try to extract a valid pool.
    Returns list of 2+ canonical skills, or None.
    """
    tokens = _split_to_tokens(fragment)
    known = _skills_from_tokens(tokens, normalize_map)
    return known if len(known) >= min_skills else None


def detect_pools(
    jd_lower: str,
    normalize_map: Dict[str, str],
) -> List[List[str]]:
    """
    Detect required_one_of pools using HIGH-CONFIDENCE patterns only.

    Pattern 1 — Trigger phrases:
        "one of Java, Python, Go"
        "proficiency in React or Angular"
        "experience with Kafka, SQS, or Kinesis"

    Pattern 2 — Explicit "X or Y" (both must be known skills):
        "Java or Golang"
        "GCP or AWS"

    Pattern 3 — Parenthetical lists where 2+ items are known skills:
        "messaging services (Kafka, SQS, SNS, Kinesis)"
        "LLM frameworks (e.g., LangChain, RAG)"
        "cloud platforms (GCP or AWS)"

    Conservative rule: a pool is only created when 2+ items validate
    against skills_master. This prevents false pools from random text.
    """
    pools: List[List[str]] = []
    seen_pools: set = set()
    candidate_pools: List[Dict] = []

    def add_pool(pool: List[str]) -> None:
        key = frozenset(s.lower() for s in pool)
        if key not in seen_pools and len(pool) >= 2:
            seen_pools.add(key)
            pools.append(pool)

    # ── Pattern 1: Trigger phrase → skill list ────────────────────────────
    for trigger in POOL_TRIGGER_PHRASES:
        search_start = 0
        while True:
            pos = jd_lower.find(trigger, search_start)
            if pos == -1:
                break
            after_trigger = jd_lower[pos + len(trigger):]
            after_trigger = _clip_to_sentence(after_trigger)

            pool = _try_pool(after_trigger, normalize_map)
            if pool:
                candidate_pools.append({
                    "skills": pool,
                    "source": "trigger",
                    "context": after_trigger
                })

            search_start = pos + 1

    # ── Pattern 2: Explicit "X or Y" — both must be known skills ─────────
    or_re = re.compile(
        r'([\w.#+\-/]+(?:\s[\w.#+\-/]+)?)\s+or\s+([\w.#+\-/]+(?:\s[\w.#+\-/]+)?)',
        re.IGNORECASE
    )
    for m in or_re.finditer(jd_lower):
        left, right = m.group(1).strip(), m.group(2).strip()
        known = _skills_from_tokens([left, right], normalize_map)
        if len(known) >= 2:
            context = jd_lower[max(0, m.start() - 40): m.end() + 40]
            candidate_pools.append({
                "skills": known,
                "source": "or",
                "context": context
            })

    # ── Pattern 3: Parenthetical "(X, Y, Z)" — 2+ known skills inside ────
    paren_re = re.compile(r'\(([^)]{3,120})\)')
    for m in paren_re.finditer(jd_lower):
        content = m.group(1)
        if ',' not in content and ' or ' not in content:
            continue
        tokens = _split_to_tokens(content)
        known = _skills_from_tokens(tokens, normalize_map)
        if len(known) >= 2:
            context = jd_lower[max(0, m.start() - 60): m.end() + 60]
            candidate_pools.append({
                "skills": known,
                "source": "paren",
                "context": context
            })

    # ── Final validation: classify and add valid candidates ───────────────
    for candidate in candidate_pools:
        if not candidate["skills"]:
            continue
        if len(candidate["skills"]) < 2:
            continue
        # Skip if context contains example phrases ("such as", "like", etc.)
        context_lower = candidate.get("context", "").lower()
        if any(phrase in context_lower for phrase in EXAMPLE_PHRASES):
            continue
        if _classify_pool_candidate(candidate):
            add_pool(candidate["skills"])

    logger.info("jd_parser_detect_pools pools_found=%d", len(pools))
    return pools


# ---------------------------------------------------------------------------
# 6. Skill classification  (authoritative — replaces extractor classification)
# ---------------------------------------------------------------------------

def _split_jd_into_sections(jd_text: str) -> Dict[str, str]:
    """
    Split JD into named sections based on common header patterns.

    Returns dict of {section_label: section_text} where section_label is one of:
        "required", "preferred", "responsibilities", "about", "unknown"

    Strategy:
    - Split on lines that look like section headers (ALL CAPS, Title Case, or
      ending with ':')
    - Classify each section by its header text
    - Preserve original text within each section for skill searching

    This is the key function that makes classify_skills() section-aware.
    """
    # Normalize line endings
    text = jd_text.replace('\r\n', '\n').replace('\r', '\n')
    lines = text.split('\n')

    # Regex: a header line is short (<= 80 chars), not a sentence (no mid-line period),
    # and matches one of: ALL CAPS, Title Case With Words, or ends with ':'
    header_re = re.compile(
        r'^[\s\*\-•#]*'           # optional leading bullets/whitespace
        r'([A-Z][^\n]{0,75})'     # content: starts uppercase, max 75 chars
        r'[\s:]*$'                # optional trailing colon/spaces
    )

    sections: Dict[str, str] = {}
    current_label = "unknown"
    current_lines: List[str] = []

    def _classify_header(header_text: str) -> str:
        h = header_text.lower().strip().rstrip(':').strip()
        # Required section headers
        for phrase in REQUIRED_SECTION_HEADERS:
            if phrase in h:
                return "required"
        # Preferred section headers
        for phrase in PREFERRED_SECTION_HEADERS:
            if phrase in h:
                return "preferred"
        # Responsibilities section
        responsibility_headers = [
            "responsibilities", "what you'll do", "what you will do",
            "your role", "the role", "role overview", "job duties",
            "duties", "key duties", "day to day", "day-to-day",
            "you will", "you'll", "about the role", "about this role",
        ]
        for phrase in responsibility_headers:
            if phrase in h:
                return "responsibilities"
        
        # Tech Stack section — treat as preferred (company tech dump, not requirements)
        tech_stack_headers = [
            "tech stack", "our tech stack", "technology stack",
            "technologies we use", "our stack", "tools we use",
            "our technologies", "technology used", "what we use",
        ]
        for phrase in tech_stack_headers:
            if phrase in h:
                return "tech_stack"
        
        return "unknown"

    for line in lines:
        # Check if this line is a section header
        stripped = line.strip()
        is_header = False

        if stripped and len(stripped) <= 80:
            # Must look like a header: ends with ':', OR is mostly uppercase,
            # OR is Title Case and short
            ends_colon = stripped.endswith(':')
            is_upper = stripped.replace(' ', '').replace(':', '').isupper() and len(stripped) > 3
            # Title Case: most words start uppercase, no lowercase-start words > 3 chars
            words = stripped.rstrip(':').split()
            titled = (
                len(words) >= 1
                and all(
                    w[0].isupper() or len(w) <= 3 or w.lower() in ('and', 'or', 'of', 'the', 'a', 'an', 'in', 'on', 'for', 'with', 'to')
                    for w in words
                    if w
                )
                and len(words) <= 8
            )
            is_header = ends_colon or is_upper or (titled and len(stripped) <= 50)

        if is_header:
            # Save current section
            if current_lines:
                section_text = '\n'.join(current_lines)
                if current_label in sections:
                    sections[current_label] += '\n' + section_text
                else:
                    sections[current_label] = section_text
            # Start new section
            current_label = _classify_header(stripped)
            current_lines = []
        else:
            current_lines.append(line)

    # Save last section
    if current_lines:
        section_text = '\n'.join(current_lines)
        if current_label in sections:
            sections[current_label] += '\n' + section_text
        else:
            sections[current_label] = section_text

    logger.debug(
        "jd_sections_split sections=%s",
        {k: len(v) for k, v in sections.items()}
    )
    return sections


def _classify_skill_by_inline_context(
    skill_lower: str,
    jd_lower: str,
    window: int = 300,
) -> Optional[str]:
    """
    Find every occurrence of skill in jd_lower and examine the surrounding
    inline context (the sentence/clause it lives in).

    Returns:
        "required"  — skill appears near a required inline signal
        "preferred" — skill appears near a preferred inline signal
        None        — no inline signal found for this skill

    Uses word-boundary matching to avoid false positives.
    """
    # Find all positions of the skill in JD text
    escaped = re.escape(skill_lower)
    pattern = r'(?<![a-z0-9])' + escaped + r'(?![a-z0-9])'

    required_hits = 0
    preferred_hits = 0

    for m in re.finditer(pattern, jd_lower, re.IGNORECASE):
        pos = m.start()
        # Extract surrounding context — look back further than forward
        # (signals usually come BEFORE the skill name)
        ctx_start = max(0, pos - window)
        ctx_end = min(len(jd_lower), pos + len(skill_lower) + 80)
        ctx = jd_lower[ctx_start:ctx_end]

        for signal in REQUIRED_INLINE_SIGNALS:
            if signal in ctx:
                required_hits += 1
                break

        for signal in PREFERRED_INLINE_SIGNALS:
            if signal in ctx:
                preferred_hits += 1
                break

    if required_hits > 0 and preferred_hits == 0:
        return "required"
    if preferred_hits > 0 and required_hits == 0:
        return "preferred"
    if required_hits > 0 and preferred_hits > 0:
        # Ambiguous: trust required
        return "required"
    return None


def classify_skills(
    all_skills: List[str],
    jd_text: str,
) -> Tuple[List[str], List[str]]:
    """
    Split all extracted skills into required and preferred based on JD context.

    Strategy (applied in order, first confident signal wins):

    PASS 1 — Section-based classification
        Split JD into named sections (Requirements, Nice to Have, etc.)
        If a skill appears ONLY in a preferred section → preferred
        If a skill appears ONLY in a required/responsibilities section → required
        If it appears in both → go to inline check

    PASS 2 — Inline signal classification
        Scan every occurrence of the skill in the full JD text.
        Check the surrounding clause for required/preferred signal phrases.
        Examples:
            "proficiency in Python" → required
            "familiarity with Docker is a plus" → preferred
            "nice to have: RAG" → preferred
            "must have: CI/CD" → required

    PASS 3 — Default
        Skills with no signal → required
        (Conservative: it's better to flag a skill as required than miss it)

    Word-boundary matching throughout — prevents "go" matching "django".

    Returns: (required_skills, preferred_skills)
    """
    if not all_skills:
        return [], []

    jd_lower = jd_text.lower()

    # ── PASS 1: Section analysis ──────────────────────────────────────────
    sections = _split_jd_into_sections(jd_text)
    section_lower = {k: v.lower() for k, v in sections.items()}

    # Build per-section skill presence maps
    # section_presence[skill] = set of section labels where skill found
    section_presence: Dict[str, set] = {}

    for skill in all_skills:
        skill_lower = skill.lower()
        found_in: set = set()
        for label, text in section_lower.items():
            if _word_boundary_search(skill_lower, text):
                found_in.add(label)
        section_presence[skill] = found_in

    required: List[str] = []
    preferred: List[str] = []
    unresolved: List[str] = []  # goes to Pass 2

    REQUIRED_SECTIONS = {"required"}
    PREFERRED_SECTIONS = {"preferred", "tech_stack"}
    DEFER_TO_INLINE_SECTIONS = {"responsibilities", "unknown"}  # Don't default to required

    for skill in all_skills:
        found_in = section_presence[skill]

        in_required_section = bool(found_in & REQUIRED_SECTIONS)
        in_preferred_section = bool(found_in & PREFERRED_SECTIONS)
        in_defer_section = bool(found_in & DEFER_TO_INLINE_SECTIONS)
        found_anywhere = bool(found_in)

        if in_required_section and not in_preferred_section:
            required.append(skill)
        elif in_preferred_section and not in_required_section:
            preferred.append(skill)
        elif in_required_section and in_preferred_section:
            # Appears in both — defer to inline check
            unresolved.append(skill)
        elif in_defer_section or not found_anywhere:
            # In defer section (responsibilities, unknown) or not found — defer to inline check
            unresolved.append(skill)
        else:
            unresolved.append(skill)

    # ── PASS 1.5: Preferred inline signal overrides section label ─────────
    # If a skill landed in required via section, but has preferred inline signal → move to preferred
    reclassified_to_preferred = []
    confirmed_required = []
    for skill in required:
        skill_lower = skill.lower()
        inline = _classify_skill_by_inline_context(skill_lower, jd_lower)
        logger.info("classify_skills_override skill=%s inline_result=%s", skill, inline)
        if inline == "preferred":
            reclassified_to_preferred.append(skill)
        else:
            confirmed_required.append(skill)
    required = confirmed_required
    preferred = preferred + reclassified_to_preferred

    # ── PASS 2: Inline signal check for unresolved skills ─────────────────
    still_unresolved: List[str] = []

    for skill in unresolved:
        skill_lower = skill.lower()
        result = _classify_skill_by_inline_context(skill_lower, jd_lower)
        if result == "required":
            required.append(skill)
        elif result == "preferred":
            preferred.append(skill)
        else:
            still_unresolved.append(skill)

    # ── PASS 3: Default — no signal found ─────────────────────────────────
    # Default to required (conservative: don't miss real requirements).
    # Exception: if the JD has a well-structured preferred section and
    # the skill wasn't found in the required section at all, it might be
    # unlabelled preferred — but we can't know for sure, so still → required.
    for skill in still_unresolved:
        required.append(skill)

    # ── Deduplication: preferred must not overlap required ─────────────────
    req_set = {s.lower() for s in required}
    preferred = [s for s in preferred if s.lower() not in req_set]

    logger.info(
        "classify_skills total=%d required=%d preferred=%d unresolved_to_default=%d",
        len(all_skills), len(required), len(preferred), len(still_unresolved),
    )
    return required, preferred


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse(jd_text: str) -> ParsedJD:
    """
    Parse raw JD text and return all rule-based fields.

    Args:
        jd_text: Raw or lightly cleaned JD text (original casing preserved
                 for experience extraction; lowercased copy used for matching)

    Returns:
        ParsedJD with all authoritative fields populated.
    """
    jd_lower = jd_text.lower()
    normalize_map = _load_normalize_map()

    work_mode = detect_work_mode(jd_lower)
    entry_level = detect_entry_level(jd_lower)
    experience = detect_experience(jd_text)       # original text — casing preserved
    red_flags = detect_red_flags(jd_lower, jd_text)
    pools = detect_pools(jd_lower, normalize_map)

    logger.info(
        "jd_parser_complete work_mode=%s entry_level=%s "
        "experience=%r red_flags=%d pools=%d",
        work_mode, entry_level, experience, len(red_flags), len(pools),
    )

    return ParsedJD(
        work_mode=work_mode,
        truly_entry_level=entry_level,
        experience_required=experience,
        red_flags=red_flags,
        required_one_of=pools,
    )