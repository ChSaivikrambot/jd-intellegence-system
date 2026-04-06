from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from app.schemas import AnalyzeRequest, AnalyzeResponse, ErrorItem, JdAnalysisPayload, WarningItem
from agents.extractor import run_extractor
from agents.resume_extractor import run_resume_skill_extractor
from agents.verifier import run_verifier, run_correction
from core.cleaning import clean_jd_text
from core.decision import apply_stage2b_decisions
from core.pdf_parser import PdfParserError, extract_pdf_text, has_extractable_text
from core.pipeline import run_full_pipeline

load_dotenv()

app = FastAPI(title="Document Intelligence API")
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE = LOG_DIR / "dev.log"


def configure_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(fmt)
        root.addHandler(stream_handler)

    file_handler_exists = any(
        isinstance(h, RotatingFileHandler) and Path(getattr(h, "baseFilename", "")) == LOG_FILE
        for h in root.handlers
    )
    if not file_handler_exists:
        fh = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)

    return logging.getLogger("doc_intelligence")


logger = configure_logging()
DEV_LOG_TOKEN = os.getenv("DEV_LOG_TOKEN", "").strip()


def parse_skills_input(raw: str | None) -> list[str]:
    if not raw:
        return []
    text = raw.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
    except Exception:
        pass
    return [s.strip() for s in text.split(",") if s.strip()]


def _error_response(code: str, message: str, *, details: dict | None = None, retryable: bool = False) -> AnalyzeResponse:
    return AnalyzeResponse(
        request_id=f"req_{datetime.now(timezone.utc).isoformat()}_{uuid4().hex[:8]}",
        status="error",
        errors=[ErrorItem(code=code, message=message, retryable=retryable, details=details)],
    )


def _validate_pdf_upload(upload: UploadFile, field_name: str) -> ErrorItem | None:
    content_type = (upload.content_type or "").lower()
    filename = (upload.filename or "").lower()
    if content_type in {"application/pdf", "application/x-pdf"}:
        return None
    if filename.endswith(".pdf"):
        return None
    return ErrorItem(
        code="PDF_INVALID_FILE",
        message=f"{field_name} must be a PDF file.",
        retryable=False,
        details={"content_type": upload.content_type, "filename": upload.filename},
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def landing_page() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "landing.html"))


@app.get("/dashboard")
def dashboard_page() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "dashboard.html"))


@app.post("/analyze-with-resume", response_model=AnalyzeResponse)
async def analyze_with_resume(
    jd_text: str | None = Form(default=None),
    skills: str = Form(default="[]"),
    jd_pdf: UploadFile | None = File(default=None),
    resume_pdf: UploadFile | None = File(default=None),
) -> AnalyzeResponse:
    request_id = f"req_{datetime.now(timezone.utc).isoformat()}_{uuid4().hex[:8]}"
    has_text_jd = bool((jd_text or "").strip())
    has_pdf_jd = jd_pdf is not None
    has_resume_pdf = resume_pdf is not None
    logger.info(
        "analyze_with_resume_request request_id=%s has_text_jd=%s has_pdf_jd=%s has_resume_pdf=%s",
        request_id,
        has_text_jd,
        has_pdf_jd,
        has_resume_pdf,
    )

    if has_text_jd == has_pdf_jd:
        logger.info("analyze_with_resume_invalid_source request_id=%s", request_id)
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one JD source: jd_text or jd_pdf."
        )

    manual_skills = parse_skills_input(skills)
    logger.info("analyze_with_resume_manual_skills request_id=%s count=%s", request_id, len(manual_skills))
    warnings: list[WarningItem] = []
    jd_clean_text = jd_text or ""

    if has_pdf_jd and jd_pdf is not None:
        logger.info(
            "analyze_with_resume_jd_pdf_received request_id=%s filename=%s content_type=%s",
            request_id,
            jd_pdf.filename,
            jd_pdf.content_type,
        )
        upload_error = _validate_pdf_upload(jd_pdf, "jd_pdf")
        if upload_error:
            logger.info(
                "analyze_with_resume_jd_pdf_invalid_upload request_id=%s filename=%s content_type=%s",
                request_id,
                jd_pdf.filename,
                jd_pdf.content_type,
            )
            return AnalyzeResponse(
                request_id=request_id,
                status="error",
                errors=[upload_error],
            )
        jd_meta = {
            "filename": jd_pdf.filename,
            "content_type": jd_pdf.content_type,
            "size_bytes": None,
        }
        try:
            jd_bytes = await jd_pdf.read()
            jd_meta["size_bytes"] = len(jd_bytes)
            logger.info(
                "analyze_with_resume_jd_pdf_parse_start request_id=%s size_bytes=%s",
                request_id,
                jd_meta["size_bytes"],
            )
            result = extract_pdf_text(jd_bytes)
            logger.info(
                "analyze_with_resume_jd_pdf_parse_ok request_id=%s page_count=%s total_chars=%s",
                request_id,
                result.page_count,
                result.total_chars,
            )
            if not has_extractable_text(result, min_chars=120):
                logger.info(
                    "analyze_with_resume_jd_pdf_empty_text request_id=%s total_chars=%s",
                    request_id,
                    result.total_chars,
                )
                return _error_response(
                    "PDF_EMPTY_TEXT",
                    "JD PDF appears scanned or has no extractable text. Paste JD text or upload searchable PDF.",
                    details=jd_meta,
                )
            jd_clean_text = result.markdown_text or "\n".join(page.text for page in result.pages)
            logger.info("analyze_with_resume_jd_text_ready request_id=%s chars=%s", request_id, len(jd_clean_text))
        except PdfParserError as e:
            logger.exception(
                "analyze_with_resume_jd_pdf_parse_failed request_id=%s code=%s meta=%s",
                request_id,
                e.code,
                jd_meta,
            )
            return _error_response(e.code, f"JD PDF error: {e.message}", details=jd_meta)
        except Exception as e:
            logger.exception("analyze_with_resume_jd_pdf_unexpected_error request_id=%s meta=%s", request_id, jd_meta)
            return _error_response(
                "PDF_PARSE_FAILED",
                "Could not extract text from JD PDF.",
                details={"error": str(e), **jd_meta},
            )
    else:
        logger.info("analyze_with_resume_jd_text_mode request_id=%s chars=%s", request_id, len(jd_clean_text))

    resume_skills: list[str] = []
    if resume_pdf is not None:
        logger.info(
            "analyze_with_resume_resume_pdf_received request_id=%s filename=%s content_type=%s",
            request_id,
            resume_pdf.filename,
            resume_pdf.content_type,
        )
        upload_error = _validate_pdf_upload(resume_pdf, "resume_pdf")
        if upload_error:
            logger.info(
                "analyze_with_resume_resume_pdf_invalid_upload request_id=%s filename=%s content_type=%s",
                request_id,
                resume_pdf.filename,
                resume_pdf.content_type,
            )
            return AnalyzeResponse(
                request_id=request_id,
                status="error",
                errors=[upload_error],
            )
        resume_meta = {
            "filename": resume_pdf.filename,
            "content_type": resume_pdf.content_type,
            "size_bytes": None,
        }
        try:
            resume_bytes = await resume_pdf.read()
            resume_meta["size_bytes"] = len(resume_bytes)
            logger.info(
                "analyze_with_resume_resume_pdf_parse_start request_id=%s size_bytes=%s",
                request_id,
                resume_meta["size_bytes"],
            )
            resume_result = extract_pdf_text(resume_bytes)
            logger.info(
                "analyze_with_resume_resume_pdf_parse_ok request_id=%s page_count=%s total_chars=%s",
                request_id,
                resume_result.page_count,
                resume_result.total_chars,
            )
            if not has_extractable_text(resume_result, min_chars=120):
                logger.info(
                    "analyze_with_resume_resume_pdf_empty_text request_id=%s total_chars=%s",
                    request_id,
                    resume_result.total_chars,
                )
                return _error_response(
                    "PDF_EMPTY_TEXT",
                    "Resume PDF appears scanned or has no extractable text. Upload searchable PDF.",
                    details=resume_meta,
                )
            logger.info("analyze_with_resume_resume_skill_extract_start request_id=%s", request_id)
            resume_skills = run_resume_skill_extractor(
                resume_result.markdown_text or "\n".join(page.text for page in resume_result.pages),
                request_id=request_id,
            )
            logger.info(
                "analyze_with_resume_resume_skill_extract_ok request_id=%s count=%s",
                request_id,
                len(resume_skills),
            )
        except PdfParserError as e:
            logger.exception(
                "analyze_with_resume_resume_pdf_parse_failed request_id=%s code=%s meta=%s",
                request_id,
                e.code,
                resume_meta,
            )
            return _error_response(e.code, f"Resume PDF error: {e.message}", details=resume_meta)
        except Exception as e:
            logger.exception(
                "analyze_with_resume_resume_skill_extract_failed request_id=%s meta=%s",
                request_id,
                resume_meta,
            )
            return _error_response(
                "RESUME_SKILL_EXTRACTION_FAILED",
                "Could not extract skills from resume.",
                details={"error": str(e), **resume_meta},
                retryable=True,
            )

    if not manual_skills and not resume_skills:
        logger.info("analyze_with_resume_invalid_profile request_id=%s", request_id)
        return _error_response(
            "INVALID_INPUT_PROFILE",
            "Provide manual skills or upload a resume PDF with extractable skills.",
        )

    skills_source = "manual"
    user_skills = manual_skills
    
    # Merge resume skills with manual skills (deduplicated)
    if resume_skills:
        # Create normalized set for deduplication
        seen = {s.lower() for s in manual_skills}
        merged = list(manual_skills)  # Start with manual skills
        for skill in resume_skills:
            if skill.lower() not in seen:
                merged.append(skill)
                seen.add(skill.lower())
        user_skills = merged
        skills_source = "resume_merged" if manual_skills else "resume"
        warnings.append(
            WarningItem(
                code="RESUME_SKILLS_USED",
                message=f"Using {len(merged)} skills ({len(resume_skills)} from resume, {len(manual_skills)} manual).",
            )
        )
        if manual_skills:
            warnings.append(
                WarningItem(
                    code="SKILLS_MERGED",
                    message="Manual skills merged with resume skills (duplicates removed).",
                )
            )
    elif resume_pdf is not None:
        warnings.append(
            WarningItem(code="RESUME_SKILLS_EMPTY", message="No usable skills were extracted from resume.")
        )

    logger.info(
        "analyze_with_resume_profile_ready request_id=%s skills_source=%s final_skills_count=%s warnings=%s",
        request_id,
        skills_source,
        len(user_skills),
        len(warnings),
    )
    response = await asyncio.to_thread(analyze, AnalyzeRequest(jd_text=jd_clean_text, skills=user_skills))
    if response.payload is not None:
        response.payload.skills_source = skills_source
    if warnings:
        response.warnings.extend(warnings)
    logger.info(
        "analyze_with_resume_done request_id=%s status=%s errors=%s warnings=%s",
        request_id,
        response.status,
        len(response.errors),
        len(response.warnings),
    )
    return response


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    """
    Stage 1: raw JD text in → structured JSON out.

    Uses shared pipeline for consistency with eval.
    """
    request_id = f"req_{datetime.now(timezone.utc).isoformat()}_{uuid4().hex[:8]}"

    try:
        jd_text = clean_jd_text(req.jd_text)
    except ValueError as e:
        logger.info(
            "analyze_invalid_input request_id=%s jd_chars=%s skills_count=%s error=%s",
            request_id,
            len(req.jd_text or ""),
            len(req.skills),
            str(e),
        )
        return AnalyzeResponse(
            request_id=request_id,
            status="error",
            errors=[ErrorItem(code="INVALID_INPUT", message=str(e), retryable=False)],
        )

    logger.info(
        "analyze_request request_id=%s jd_chars=%s skills_count=%s",
        request_id,
        len(jd_text),
        len(req.skills),
    )

    warnings: list[WarningItem] = []
    
    try:
        # Use shared pipeline for full processing
        from core.pipeline import run_full_pipeline
        
        result = run_full_pipeline(
            jd_text=jd_text,
            user_skills=req.skills,
            request_id=request_id,
            max_extractor_attempts=3,
            apply_verifier=True,
            apply_smart_retry=True,
        )
        
        payload = result.payload
        warnings = result.warnings
        
    except RuntimeError as e:
        if "Extractor failed" in str(e) or "Extractor returned None" in str(e):
            return AnalyzeResponse(
                request_id=request_id,
                status="error",
                errors=[
                    ErrorItem(
                        code="EXTRACTION_SCHEMA_INVALID",
                        message="Model output could not be validated after retries.",
                        retryable=True,
                    )
                ],
            )
        raise
    except Exception as e:
        logger.exception("analyze_pipeline_error request_id=%s", request_id)
        return AnalyzeResponse(
            request_id=request_id,
            status="error",
            errors=[ErrorItem(code="PIPELINE_ERROR", message=str(e), retryable=True)],
        )

    logger.info("analyze_ok request_id=%s", request_id)
    return AnalyzeResponse(request_id=request_id, status="ok", payload=payload, warnings=warnings)