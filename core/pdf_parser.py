"""
PDF parsing utilities (Phase 1 Day 1).

This module should be runnable/usable in isolation (no agents, no LLM).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple


@dataclass(frozen=True)
class PdfPageText:
    page_number: int
    text: str


@dataclass(frozen=True)
class PdfExtractionResult:
    page_count: int
    pages: List[PdfPageText]
    total_chars: int


def extract_pdf_text(file_bytes: bytes) -> PdfExtractionResult:
    """
    Extract per-page text using PyMuPDF.

    Day 1 will implement this fully; for now this is a placeholder so imports work.
    """
    raise NotImplementedError("Implement Day 1: extract_pdf_text using PyMuPDF.")


def has_extractable_text(result: PdfExtractionResult, min_chars: int = 200) -> bool:
    return result.total_chars >= min_chars

