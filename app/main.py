from __future__ import annotations

from datetime import datetime, timezone
import logging
from uuid import uuid4

from fastapi import FastAPI
from dotenv import load_dotenv

from app.schemas import AnalyzeRequest, AnalyzeResponse, ErrorItem, JdAnalysisPayload, WarningItem
from agents.extractor import run_extractor
from agents.verifier import run_verifier
from core.cleaning import clean_jd_text
from core.decision import apply_stage2b_decisions

load_dotenv()

app = FastAPI(title="Document Intelligence API")

logger = logging.getLogger("doc_intelligence")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

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


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


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

    # Stage 2C v1: verify key extracted fields against source JD.
    try:
        verifications = run_verifier(jd_text, payload.model_dump(), request_id=request_id)
        payload.verification = verifications

        if not verifications:
            payload.confidence = downgrade_confidence(payload.confidence, "low")
            warnings.append(
                WarningItem(
                    code="VERIFICATION_EMPTY",
                    message="Verifier returned no checks; confidence downgraded.",
                )
            )

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

