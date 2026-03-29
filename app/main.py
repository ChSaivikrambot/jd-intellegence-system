from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
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

CONFIDENCE_PRIORITY = {
    "failed": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
}


def downgrade_confidence(current: str | None, new: str) -> str:
    # Keep the more conservative confidence level.
    if current is None:
        return new
    if CONFIDENCE_PRIORITY[new] < CONFIDENCE_PRIORITY[current]:
        return new
    return current


def apply_verification_guardrails(payload: JdAnalysisPayload, verifications: list, warnings: list) -> None:
    """Helper to apply confidence downgrades and warnings based on verifier results."""
    payload.verification = verifications

    if not verifications:
        payload.confidence = downgrade_confidence(payload.confidence, "low")
        warnings.append(WarningItem(code="VERIFICATION_EMPTY", message="Verifier returned no checks."))
        return

    failed_fields = [v for v in verifications if not v.verified]
    if len(failed_fields) >= 3:
        payload.confidence = downgrade_confidence(payload.confidence, "failed")
        warnings.append(
            WarningItem(
                code="VERIFICATION_FAILED",
                message="Multiple extracted fields could not be verified against JD text.",
                details={"failed_fields": [v.field for v in failed_fields]},
            )
        )
    elif len(failed_fields) >= 2:
        payload.confidence = downgrade_confidence(payload.confidence, "low")
        warnings.append(
            WarningItem(
                code="VERIFICATION_FAILED",
                message="Some extracted fields could not be verified against JD text.",
                details={"failed_fields": [v.field for v in failed_fields]},
            )
        )

    req_ver = next((v for v in verifications if v.field == "required_skills"), None)
    if req_ver and not req_ver.verified:
        payload.confidence = downgrade_confidence(payload.confidence, "low")


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


def _assert_dev_logs_access(request: Request, token: str | None) -> None:
    host = (request.client.host if request.client else "") or ""
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(status_code=403, detail="Dev logs are only accessible from localhost.")
    if DEV_LOG_TOKEN and token != DEV_LOG_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid dev log token.")


@app.get("/dev/logs")
def get_dev_logs(
    request: Request,
    tail: int = Query(default=200, ge=1, le=2000),
    x_dev_log_token: str | None = Header(default=None),
) -> JSONResponse:
    _assert_dev_logs_access(request, x_dev_log_token)
    try:
        content = LOG_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return JSONResponse({"lines": [], "count": 0, "path": str(LOG_FILE)})

    lines = content.splitlines()
    sliced = lines[-tail:]
    return JSONResponse({"lines": sliced, "count": len(sliced), "path": str(LOG_FILE)})


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
        return AnalyzeResponse(
            request_id=request_id,
            status="error",
            errors=[
                ErrorItem(
                    code="INVALID_INPUT",
                    message="Provide exactly one JD source: jd_text or jd_pdf.",
                    retryable=False,
                )
            ],
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
    if resume_skills:
        user_skills = resume_skills
        skills_source = "resume"
        warnings.append(
            WarningItem(
                code="RESUME_SKILLS_USED",
                message=f"Using {len(resume_skills)} extracted resume skills for matching.",
            )
        )
        if manual_skills:
            warnings.append(
                WarningItem(
                    code="MANUAL_SKILLS_OVERRIDDEN",
                    message="Manual skills were provided but resume skills were used as primary profile.",
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
    response = analyze(AnalyzeRequest(jd_text=jd_clean_text, skills=user_skills))
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

    No PDFs, no LLM calls, no verifier yet.
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
    payload: JdAnalysisPayload | None = None
    last_raw = ""
    for attempt in (1, 2, 3):
        try:
            payload, last_raw = run_extractor(jd_text, request_id=request_id, attempt=attempt)
        except Exception as e:
            logger.info("analyze_llm_error request_id=%s attempt=%s error=%s", request_id, attempt, str(e))
            return AnalyzeResponse(
                request_id=request_id,
                status="error",
                errors=[
                    ErrorItem(
                        code="LLM_PROVIDER_UNAVAILABLE",
                        message="LLM provider is unavailable or misconfigured.",
                        retryable=True,
                        details={"attempt": attempt, "error": str(e)},
                    )
                ],
            )
        if payload is not None:
            break

    if payload is None:
        return AnalyzeResponse(
            request_id=request_id,
            status="error",
            errors=[
                ErrorItem(
                    code="EXTRACTION_SCHEMA_INVALID",
                    message="Model output could not be validated after retries.",
                    retryable=True,
                    details={"raw": last_raw[:2000]},
                )
            ],
        )

    # Stage 2B: deterministic decision layer (no LLM).
    payload = apply_stage2b_decisions(payload, req.skills)

    # Stage 2C v1: Verify key extracted fields
    try:
        verifications = run_verifier(jd_text, payload.model_dump(), request_id=request_id)
        apply_verification_guardrails(payload, verifications, warnings)
        
        # --- STAGE 2C v2: SMART RETRY ---
        RETRYABLE_FIELDS = {"role", "required_skills", "work_mode", "experience_required"}
        failed_fields = [v.field for v in verifications if not v.verified]
        retryable_failed = [f for f in failed_fields if f in RETRYABLE_FIELDS]

        if 1 <= len(retryable_failed) <= 2:
            logger.info("smart_retry_triggered request_id=%s fields=%s", request_id, retryable_failed)
            try:
                # 1. Run Correction
                corrections = run_correction(jd_text, payload.model_dump(), retryable_failed, request_id=request_id)
                
                # 2. Merge safely (only overwrite failed fields)
                for field in retryable_failed:
                    if field in corrections:
                        setattr(payload, field, corrections[field])
                
                # 3. Mark retry usage (Assumes payload schema supports this field)
                if hasattr(payload, "retries_used"):
                    payload.retries_used += 1
                warnings.append(WarningItem(code="RETRY_APPLIED", message=f"Corrected fields: {retryable_failed}"))
                
                # 4. Re-run Stage 2B (Decisions)
                payload = apply_stage2b_decisions(payload, req.skills)
                
                # 5. Re-Verify & Re-apply Guardrails
                new_verifications = run_verifier(jd_text, payload.model_dump(), request_id=request_id)
                # Clear previous verification warnings so we don't double up
                warnings = [w for w in warnings if w.code not in ("VERIFICATION_FAILED", "VERIFICATION_EMPTY")]
                apply_verification_guardrails(payload, new_verifications, warnings)

            except Exception as e:
                logger.error("smart_retry_failed request_id=%s error=%s", request_id, str(e))
                warnings.append(WarningItem(
                    code="RETRY_FAILED", 
                    message="Smart retry attempted but failed.", 
                    details={"error": str(e)}
                ))
        # --- END SMART RETRY ---

    except Exception as e:
        warnings.append(
            WarningItem(
                code="VERIFIER_UNAVAILABLE",
                message="Verifier step failed; returning unverified output.",
                details={"error": str(e)},
            )
        )

    logger.info("analyze_ok request_id=%s", request_id)
    return AnalyzeResponse(request_id=request_id, status="ok", payload=payload, warnings=warnings)