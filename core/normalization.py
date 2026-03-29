from __future__ import annotations

from typing import Literal, Optional


WorkMode = Literal["remote", "hybrid", "onsite", "not_mentioned"]


def normalize_work_mode(raw: Optional[str]) -> Optional[WorkMode]:
    if raw is None:
        return None
    s = raw.strip().lower()
    if not s:
        return None

    # Normalize common variants first.
    if s in {"wfh", "work from home", "work-from-home", "remote role", "remote"}:
        return "remote"
    if s in {"hybrid", "partly remote", "remote/hybrid"}:
        return "hybrid"
    if s in {"onsite", "on-site", "on site", "in office", "in-office"}:
        return "onsite"
    if s in {"na", "n/a", "not mentioned", "not_mentioned", "unknown"}:
        return "not_mentioned"

    # Best-effort keyword fallback.
    if "remote" in s or "wfh" in s:
        return "remote"
    if "hybrid" in s:
        return "hybrid"
    if "onsite" in s or "on site" in s or "office" in s:
        return "onsite"
    return None

