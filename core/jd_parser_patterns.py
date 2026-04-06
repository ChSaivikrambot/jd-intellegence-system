"""
Pattern definitions for jd_parser.py.

All keyword lists, phrase lists, and compiled regex patterns live here.
jd_parser.py imports from this file — keeping the logic and the data separate.
Extend this file when you encounter new real-world JD phrasings.

All string values here are LOWERCASE — jd_parser normalizes JD text to
lowercase before matching, so patterns must match that.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Work mode
# ---------------------------------------------------------------------------

REMOTE_PHRASES = [
    "fully remote",
    "100% remote",
    "remote only",
    "remote-first",
    "remote first",
    "remote position",
    "remote role",
    "remote opportunity",
    "work from home",
    "work-from-home",
    "wfh",
    "work remotely",
    "remote work",
    "remote working",
    "remote job",
    "permanently remote",
    "location: remote",
    "location : remote",
    "anywhere in",
]

HYBRID_PHRASES = [
    "hybrid",
    "partial remote",
    "partially remote",
    "flexible working",
    "flexible work arrangement",
    "remote and in-office",
    "remote/in-office",
    "mix of remote",
    "blend of remote",
    "few days a week",
    "days per week in office",
    "days in office",
    "days from office",
    "days on-site",
]

ONSITE_PHRASES = [
    "on-site only",
    "onsite only",
    "must work from office",
    "required to work from office",
    "no remote work",
    "no remote option",
    "not a remote",
    "in-person",
    "in person",
    "in-office",
    "in office",
    "office based",
    "office-based",
    "on-site",
    "onsite",
    "on location",
    "on premises",
    "on-premises",
    "on-prem",
    "work from our office",
    "work from office",
    "wfo",
]

# ---------------------------------------------------------------------------
# Entry level / Internship
# ---------------------------------------------------------------------------

ENTRY_LEVEL_PHRASES = [
    "internship",
    "intern ",
    " intern",
    "stipend",
    "trainee",
    "graduate trainee",
    "apprentice",
    "fresher",
    "freshers",
    "fresh graduate",
    "recent graduate",
    "new graduate",
    "college graduate",
    "university graduate",
    "campus hire",
    "campus recruitment",
    "campus placement",
    "entry level",
    "entry-level",
    "no experience required",
    "no prior experience",
    "no work experience required",
    "0 years",
    "0-1 year",
    "0 to 1 year",
    "zero to one year",
    "final year student",
    "final-year student",
    "penultimate year",
    "pursuing a degree",
    "currently enrolled",
    "junior developer",
    "junior engineer",
    "junior software",
    "associate engineer",
    "associate developer",
    "associate software",
]

# ---------------------------------------------------------------------------
# Experience — compiled regex patterns
# ---------------------------------------------------------------------------

EXPERIENCE_REGEXES = [
    re.compile(
        r'\b(\d+\s*[-–]\s*\d+)\s*(?:years?|yrs?)\b',
        re.IGNORECASE
    ),
    re.compile(
        r'\b(\d+\s*\+)\s*(?:years?|yrs?)\b',
        re.IGNORECASE
    ),
    re.compile(
        r'\b(?:minimum|at\s+least|min\.?|atleast)\s+(\d+)\s*(?:years?|yrs?)\b',
        re.IGNORECASE
    ),
    re.compile(
        r'\b(\d+)\s*(?:years?|yrs?)\s+(?:of\s+)?(?:experience|exp(?:erience)?)\b',
        re.IGNORECASE
    ),
    re.compile(
        r'\bexperience\s+of\s+(\d+)\s*(?:years?|yrs?)\b',
        re.IGNORECASE
    ),
    re.compile(
        r'\b(\d+)\s*(?:years?|yrs?)\b',
        re.IGNORECASE
    ),
]

# ---------------------------------------------------------------------------
# Red flags — leadership / management
# ---------------------------------------------------------------------------

RED_FLAG_LEADERSHIP = [
    "direct reports",
    "direct report",
    "hiring decisions",
    "hiring and firing",
    "performance reviews",
    "performance appraisals",
    "manage a team",
    "managing a team",
    "manage the team",
    "managing the team",
    "manage engineers",
    "managing engineers",
    "manage developers",
    "managing developers",
    "manage and mentor",
    "people management",
    "team management",
    "team handling",
    "handle a team",
    "handling a team",
    "lead a team",
    "leading a team",
    "lead and manage",
    "grow the team",
    "build and manage",
    "build and lead",
    "build a team",
    "head of engineering",
    "head of the engineering",
    "head of department",
    "engineering manager",
    "manage cross-functional",
    "manage cross functional",
]

# ---------------------------------------------------------------------------
# Red flags — degree requirements
# ---------------------------------------------------------------------------

RED_FLAG_DEGREE = [
    "bachelor's degree required",
    "bachelor degree required",
    "bachelors degree required",
    "b.tech required",
    "be required",
    "master's degree required",
    "master degree required",
    "masters degree required",
    "m.tech required",
    "phd required",
    "phd is required",
    "phd mandatory",
    "doctorate required",
    "degree is mandatory",
    "degree is required",
    "must have a degree",
    "must hold a degree",
]

# ---------------------------------------------------------------------------
# Red flags — explicit seniority titles in JD body text
# ---------------------------------------------------------------------------

RED_FLAG_SENIORITY = [
    "senior software engineer",
    "senior developer",
    "senior engineer",
    "lead engineer",
    "lead developer",
    "principal engineer",
    "principal developer",
    "staff engineer",
    "tech lead",
    "technical lead",
    "solutions architect",
    "engineering manager",
]

# ---------------------------------------------------------------------------
# Pool trigger phrases
# ---------------------------------------------------------------------------

POOL_TRIGGER_PHRASES = [
    "one or more of the following",
    "one or more of",
    "at least one of the following",
    "at least one of",
    "one of the following",
    "any of the following",
    "one of",
    "any of",
    "either of",
    "or one of",
    "if you have experience with",
    "if you know",
    "if you have knowledge of",
    "familiarity with any of",
    "familiarity with one of",
    "exposure to any of",
    "experience with any of",
    "experience in one of",
    "knowledge of any of",
    "proficiency in",
    "strong proficiency in",
    "fluency in",
    "expertise in",
    "strong knowledge of",
    "deep knowledge of",
    "hands-on experience with",
    "hands on experience with",
    "working knowledge of",
    "solid understanding of",
    "experience working with",
    "experience programming in",
    "experience writing in",
    "experience developing in",
    "experience building in",
    "experience with",
    "skilled in",
    "strong background in",
]

# ---------------------------------------------------------------------------
# NEW — Section header classification for classify_skills()
# ---------------------------------------------------------------------------
# These are matched against lowercased, colon-stripped section header lines.
# Ordered most-specific → least-specific within each group.

REQUIRED_SECTION_HEADERS: list[str] = [
    # Explicit required / must-have headers
    "required skills",
    "required qualifications",
    "required experience",
    "required technical skills",
    "required competencies",
    "minimum qualifications",
    "minimum requirements",
    "minimum skills",
    "must have skills",
    "must have",
    "mandatory skills",
    "mandatory requirements",
    "mandatory qualifications",
    "basic qualifications",
    "basic requirements",
    "core skills",
    "core requirements",
    "core competencies",
    "essential skills",
    "essential requirements",
    "essential qualifications",
    "key requirements",
    "key skills",
    "key qualifications",
    "technical requirements",
    "technical skills required",
    "technical skills",         # standalone header — almost always required
    "skills required",
    "skills & qualifications",
    "skills and qualifications",
    "qualifications",
    "requirements",
    "what we're looking for",
    "what we are looking for",
    "what you need",
    "what you'll need",
    "what you will need",
    "you need",
    "you must have",
    "you should have",
    "you will have",
    "ideal candidate",
    "the ideal candidate",
    "candidate profile",
    "who you are",
    "who we're looking for",
    "who we are looking for",
    "job requirements",
    "position requirements",
    "role requirements",
    "experience required",
    "experience & skills",
    "experience and skills",
    "background required",
    "background & skills",
]

PREFERRED_SECTION_HEADERS: list[str] = [
    # Explicit preferred / nice-to-have headers
    "preferred skills",
    "preferred qualifications",
    "preferred experience",
    "preferred requirements",
    "preferred competencies",
    "nice to have",
    "nice-to-have",
    "nice to have skills",
    "good to have",
    "good to have skills",
    "good-to-have",
    "bonus skills",
    "bonus points",
    "bonus qualifications",
    "bonus experience",
    "added advantage",
    "added bonus",
    "additional skills",
    "additional qualifications",
    "additional requirements",
    "additional nice to have",
    "desirable skills",
    "desirable qualifications",
    "desirable experience",
    "desirable",
    "advantageous",
    "would be a plus",
    "will be a plus",
    "a plus",
    "a bonus",
    "an advantage",
    "an added advantage",
    "optional skills",
    "optional requirements",
    "not required but",
    "not mandatory but",
    "it would be great",
    "it would be nice",
    "ideally you have",
    "ideally you will have",
    "plus if you have",
    "plus if you know",
    "plus points",
    "extra points",
    "brownie points",
    "standout qualifications",
    "what sets you apart",
    "what will make you stand out",
    "what makes you stand out",
    "we'd love it if",
    "we would love it if",
    "we'd be excited if",
    "we also value",
    "exposure to",                # "Exposure to cloud platforms" → preferred section
    "familiarity with",           # "Familiarity with Docker" → preferred section
]

# ---------------------------------------------------------------------------
# NEW — Inline required signals for classify_skills() Pass 2
# ---------------------------------------------------------------------------
# These phrases, when found in the SAME clause/sentence as a skill,
# strongly indicate the skill is REQUIRED.
# Ordered most-specific → least-specific.

REQUIRED_INLINE_SIGNALS: list[str] = [
    # Explicit must/mandatory language
    "must have",
    "must know",
    "must be proficient",
    "must possess",
    "must demonstrate",
    "must include",
    "is required",
    "are required",
    "is mandatory",
    "are mandatory",
    "mandatory",
    "is essential",
    "are essential",
    "essential",
    "is necessary",
    "are necessary",
    "necessary",
    "non-negotiable",
    "hard requirement",
    "hard requirements",

    # Strong proficiency language
    "strong proficiency in",
    "strong proficiency with",
    "proficiency in",
    "proficiency with",
    "strong knowledge of",
    "strong knowledge in",
    "deep knowledge of",
    "deep knowledge in",
    "expertise in",
    "expertise with",
    "mastery of",
    "mastery in",
    "advanced knowledge",
    "advanced experience",
    "advanced proficiency",
    "hands-on experience",
    "hands on experience",
    "solid experience",
    "proven experience",
    "demonstrated experience",
    "extensive experience",
    "strong experience",
    "relevant experience",
    "solid understanding",
    "strong understanding",
    "in-depth knowledge",
    "in depth knowledge",

    # Role-context language (implies required)
    "you will use",
    "you'll use",
    "you will work with",
    "you'll work with",
    "you will develop",
    "you'll develop",
    "you will build",
    "you'll build",
    "you will design",
    "you'll design",
    "you will implement",
    "you'll implement",
    "you will be responsible",
    "you'll be responsible",
    "the role requires",
    "this role requires",
    "position requires",
    "job requires",
    "we require",
    "we need",
    "we expect",
    "we are looking for",
    "we're looking for",
    "looking for someone with",
    "looking for a candidate with",
    "candidate should have",
    "candidate must have",
    "should have experience",
    "should have strong",
    "should have working knowledge",
    "should have proficiency",
    "should have expertise",
    "should be proficient",
    "should be experienced",
    "should be familiar",   # weaker but still required signal
    "expected to",
    "required to",
    "ability to",           # weak — only counts if no preferred signal also present
    "capable of",
    "competency in",
    "competence in",

    # "X years of Y" is implicitly required
    "years of experience in",
    "years of experience with",
    "years experience in",
    "years experience with",
    "year of experience in",
    "year of experience with",

    # Section-like inline labels
    "required:",
    "requirements:",
    "mandatory:",
    "must:",
]

# ---------------------------------------------------------------------------
# NEW — Inline preferred signals for classify_skills() Pass 2
# ---------------------------------------------------------------------------
# These phrases, when found in the SAME clause/sentence as a skill,
# strongly indicate the skill is PREFERRED (not required).

PREFERRED_INLINE_SIGNALS: list[str] = [
    # Explicit nice-to-have / preferred language
    "nice to have",
    "nice-to-have",
    "good to have",
    "good-to-have",
    "preferred",
    "preferably",
    "is a plus",
    "are a plus",
    "would be a plus",
    "will be a plus",
    "is a bonus",
    "are a bonus",
    "would be a bonus",
    "is a nice addition",
    "would be nice",
    "is advantageous",
    "would be advantageous",
    "is an advantage",
    "would be an advantage",
    "added advantage",
    "added bonus",
    "bonus points",
    "extra points",
    "brownie points",

    # Soft / optional language
    "familiarity with",
    "familiar with",
    "basic familiarity",
    "exposure to",
    "some exposure",
    "awareness of",
    "awareness in",
    "understanding of",         # softer than "solid understanding of"
    "basic understanding",
    "basic knowledge",
    "general knowledge",
    "knowledge of",             # softer form — no "strong/deep" prefix
    "knowledge in",
    "interest in",
    "interest with",
    "ideally",
    "ideally you",
    "ideally have",
    "ideally will have",
    "ideally with",
    "not required but",
    "not mandatory but",
    "optional",
    "optionally",
    "if you have",
    "if you know",
    "if applicable",
    "where applicable",
    "as needed",
    "when needed",
    "could be",
    "may be",
    "might be",
    "can be",
    "desirable",
    "desired",
    "it would be great",
    "it would be nice",
    "we'd love",
    "we would love",
    "we'd be excited",
    "we would be excited",
    "excited if you",
    "standout",
    "what sets you apart",
    "make you stand out",
    "sets you apart",

    # Section-like inline labels (preferred variants)
    "preferred:",
    "nice to have:",
    "good to have:",
    "bonus:",
    "optional:",
    "desirable:",
    "advantageous:",
    "plus:",
]

# ---------------------------------------------------------------------------
# NEW — Example/illustrative phrases that should NOT trigger pools
# ---------------------------------------------------------------------------
# Phrases like "such as" introduce examples, not "pick one" alternatives.
# In detect_pools(), skip any candidate where the context was preceded by
# one of these phrases.

EXAMPLE_PHRASES: list[str] = [
    "such as",
    "like",           # "technologies like X, Y, Z" — examples, not pools
    "including",      # "including X, Y, Z" — examples
    "e.g.",           # explicit "for example"
    "eg.",
    "for example",
    "for instance",
]