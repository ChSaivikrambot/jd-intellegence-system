"""
Text chunking utilities (Phase 1 Day 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class TextChunk:
    chunk_id: str
    page_range: str
    text: str


def chunk_pages(pages: List[dict], chunk_size_chars: int = 4000) -> List[TextChunk]:
    """
    Placeholder chunker. Day 1 will implement a page-aware chunker.
    """
    raise NotImplementedError("Implement Day 1: chunker with page mapping.")

