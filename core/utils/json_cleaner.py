from __future__ import annotations

import re


def strip_code_fences(text: str) -> str:
    """
    Removes markdown code fences like ```json ... ``` around JSON.
    Keeps the inner content intact.
    """
    if not text:
        return text

    # Remove opening fences like ```json\n or ```\n
    cleaned = re.sub(r"^\s*```[a-zA-Z]*\s*\n?", "", text.strip())
    # Remove closing fence ``` (typically at end, but do it safely)
    cleaned = cleaned.replace("```", "")
    return cleaned.strip()

