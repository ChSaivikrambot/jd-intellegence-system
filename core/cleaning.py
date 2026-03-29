from __future__ import annotations

import re


_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    # Minimal HTML removal for pasted JDs.
    return _TAG_RE.sub(" ", text)


def normalize_whitespace(text: str) -> str:
    return " ".join(text.split()).strip()


def clean_jd_text(jd_text: str, *, max_chars: int = 50_000) -> str:
    cleaned = normalize_whitespace(strip_html(jd_text))
    if not cleaned:
        raise ValueError("JD text is empty after cleaning.")
    if len(cleaned) > max_chars:
        raise ValueError(f"JD text too long after cleaning (max {max_chars} chars).")
    return cleaned

