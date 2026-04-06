from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from groq import Groq


@dataclass(frozen=True)
class GroqSettings:
    api_key: str
    model: str


logger = logging.getLogger("doc_intelligence.groq")


def call_groq_text(settings: GroqSettings, prompt: str, *, timeout_s: int = 60) -> str:
    """
    Low-level Groq call that returns raw text with latency logging.

    Parsing/validation MUST live outside this module.
    """
    start = time.perf_counter()
    client = Groq(api_key=settings.api_key, timeout=timeout_s)
    completion = client.chat.completions.create(
        model=settings.model,
        messages=[
            {"role": "system", "content": "You are a strict JSON generator."},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
    )

    content: Optional[str] = completion.choices[0].message.content
    duration_ms = int((time.perf_counter() - start) * 1000)
    
    if not content:
        logger.warning("groq_empty_response model=%s duration_ms=%s", settings.model, duration_ms)
        raise RuntimeError("Empty response from Groq.")
    
    logger.info("groq_call_ok model=%s duration_ms=%s chars=%s", settings.model, duration_ms, len(content))
    return content

