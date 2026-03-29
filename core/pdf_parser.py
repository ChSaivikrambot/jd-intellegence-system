"""
PDF parsing utilities (Phase 1 Day 1).

This module should be runnable/usable in isolation (no agents, no LLM).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List
import tempfile


class PdfParserError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PdfPageText:
    page_number: int
    text: str


@dataclass(frozen=True)
class PdfExtractionResult:
    page_count: int
    pages: List[PdfPageText]
    total_chars: int
    markdown_text: str


def _extract_markdown(file_bytes: bytes, doc: Any) -> str:
    try:
        import pymupdf4llm
    except Exception as e:
        raise PdfParserError("PDF_MARKDOWN_DEPENDENCY_MISSING", "pymupdf4llm is not installed.") from e

    try:
        markdown = pymupdf4llm.to_markdown(doc)
        if isinstance(markdown, str) and markdown.strip():
            return markdown
    except Exception:
        pass

    # Fallback path for versions expecting file path input.
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
            tmp.write(file_bytes)
            tmp.flush()
            markdown = pymupdf4llm.to_markdown(tmp.name)
            if isinstance(markdown, str):
                return markdown
    except Exception as e:
        raise PdfParserError("PDF_PARSE_FAILED", "Could not convert PDF to markdown.") from e
    raise PdfParserError("PDF_PARSE_FAILED", "Could not convert PDF to markdown.")


def extract_pdf_text(
    file_bytes: bytes,
    *,
    max_bytes: int = 10 * 1024 * 1024,
    max_pages: int = 30,
) -> PdfExtractionResult:
    """
    Extract per-page text + markdown using PyMuPDF/PyMuPDF4LLM.

    Raises PdfParserError with stable codes for API error mapping.
    """
    if not file_bytes:
        raise PdfParserError("PDF_INVALID_FILE", "Empty PDF file.")
    if len(file_bytes) > max_bytes:
        raise PdfParserError("PDF_TOO_LARGE", f"PDF exceeds size limit ({max_bytes} bytes).")

    try:
        import fitz
    except Exception as e:
        raise PdfParserError("PDF_DEPENDENCY_MISSING", "PyMuPDF is not installed in this Python environment.") from e

    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as e:
        raise PdfParserError("PDF_INVALID_FILE", "Invalid or unreadable PDF.") from e

    if doc.page_count <= 0:
        doc.close()
        raise PdfParserError("PDF_INVALID_FILE", "PDF has no pages.")
    if doc.page_count > max_pages:
        doc.close()
        raise PdfParserError("PDF_TOO_LARGE", f"PDF exceeds page limit ({max_pages} pages).")

    pages: List[PdfPageText] = []
    total_chars = 0
    for idx, page in enumerate(doc, start=1):
        text = page.get_text("text") or ""
        pages.append(PdfPageText(page_number=idx, text=text))
        total_chars += len(text)

    markdown = _extract_markdown(file_bytes, doc)
    doc.close()

    return PdfExtractionResult(
        page_count=len(pages),
        pages=pages,
        total_chars=total_chars,
        markdown_text=markdown,
    )


def has_extractable_text(result: PdfExtractionResult, min_chars: int = 200) -> bool:
    return result.total_chars >= min_chars

