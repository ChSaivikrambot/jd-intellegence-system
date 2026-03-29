# JD Intelligence API — Final Locked Architecture (Phases 0–7)

This document locks the final architecture **before** coding features. It mirrors the locked visual spec in `docs/ijd_intelligence_final_locked_architecture.html`.

## Scope (Phase 0–7)

- **Goal**: Take a Job Description (JD) + a user profile (skills) and return:
  - structured JD extraction (schema-validated)
  - evidence/verification map for claims
  - deterministic skill-gap analysis
  - deterministic recommendation + confidence
- **LLM calls**:
  - **Normal**: 2 calls (Extractor + Verifier)
  - **Max**: 6 calls (retries)
  - **Optional Mode C adds**: +1 LLM call (resume → skills)
- **Non-goals (Phase 1 build)**:
  - accounts/persistence (profile can be per-session or per-request)
  - full MLOps gates (Phase 7 is Phase 2 build)

## API surface

### `GET /health`

Returns:

```json
{ "status": "ok" }
```

### `POST /analyze`

Two JD input modes + manual skills:

- **mode A**: paste raw JD text
- **mode B**: upload JD PDF (PyMuPDF extract)
- **user profile**: `skills: ["Python", "FastAPI", "Docker"]` (sent per request or stored in session)

### `POST /analyze-with-resume` (new, optional)

Same as `POST /analyze`, plus **Mode C**:

- **mode C**: upload resume PDF
  - extract resume text (PyMuPDF)
  - reuse **Extractor Agent** to extract `skills[]` from resume (**LLM call 3**)
  - feed extracted `skills[]` into **Phase 3** as the user profile

## Phase breakdown (must remain identical to locked HTML)

### Phase 0 — input

1. **Accept inputs** via API:
   - `POST /analyze`: mode A (raw JD text) or mode B (JD PDF)
   - `POST /analyze-with-resume`: mode A/B plus mode C (resume PDF)
2. **User profile**:
   - A/B: manual skills list
   - C: skills extracted from resume (optional; acts as profile source)
3. **Validation + cleaning** (core utilities):
   - if PDF → PyMuPDF extract
   - strip HTML tags/extra whitespace
   - enforce token/length cap (reject if > 6000 tokens equivalent)

### Phase 1 — extraction (LLM call 1)

4. Send clean JD text to Groq — **Extractor Agent**
5. Extractor returns domain JSON fields such as:
   - `company`, `role`, `truly_entry_level`, `required_skills`, `preferred_skills`,
     `experience_required`, `red_flags`, `compensation`, `work_mode`
6. **Pydantic v2 validation**:
   - pass → Phase 2
   - fail → retry with stricter prompt (max 2 retries)

### Phase 2 — verification (LLM call 2)

7. Send original JD + extracted JSON to **Verifier Agent**
8. Verifier returns evidence map per field (find exact evidence in original JD text)
9. If not all claims verified:
   - smart retry Extractor with field-level feedback
   - max 2 retries total across validation types (as per HTML spec)

### Phase 3 — skill gap analysis (deterministic, zero LLM)

10. Compare extracted `required_skills` vs user profile skills (pure Python set logic)

### Phase 4 — confidence + recommendation (deterministic, zero LLM)

11. Assign confidence deterministically based on verification + retries used
12. Assign recommendation deterministically using if/else based on `red_flags`, match score, and `truly_entry_level`

### Phase 5 — final response (never crashes)

13. Return complete structured JSON (Pydantic guaranteed)

### Phase 6 — frontend (single HTML)

14. Simple single-page UI (no React required) for pasting JD + entering skills once

### Phase 7 — MLOps + deployment (Phase 2 build)

15. Eval dataset + automated scoring + CI/CD gates + Docker/Railway deployment

## Mode C — resume skill extraction (the only addition)

Mode C is the only architecture change. Everything in Phases 1–7 stays identical.

- **Input**: resume PDF (extract text with PyMuPDF)
- **LLM call**: reuse Extractor Agent with a resume-skill schema; output is just `skills[]`
- **Output usage**: the extracted `skills[]` becomes the user profile for Phase 3

