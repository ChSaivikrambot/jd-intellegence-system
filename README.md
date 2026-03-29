# doc-intelligence

JD Intelligence API (locked architecture) — Job Description input → verified structured JSON → deterministic skill-gap + recommendation.

This repo is currently **scaffold-first**: the architecture is locked in `ARCHITECTURE.md`, and most runtime code is still placeholders.

## What’s locked (architecture)

- **Phases 0–7** are documented in `ARCHITECTURE.md` and mirrored in `docs/ijd_intelligence_final_locked_architecture.html`.
- **Normal LLM calls**: 2 (Extractor + Verifier)
- **Max LLM calls (retries)**: 6
- **Zero LLM for decisions**: skill-gap + recommendation are deterministic

## Endpoints (Phase 0 input)

- `GET /health`: liveness check
- `POST /analyze`: analyze a JD with user skills
  - mode A: paste raw JD text
  - mode B: upload JD PDF
  - user profile: skills provided manually (per request or session)
- `POST /analyze-with-resume` (new, optional): analyze JD + upload resume PDF
  - mode C: upload resume PDF → extract skills (LLM call 3, reuses Extractor Agent) → feed skills into Phase 3 user profile

## Folder structure (kept intentionally simple)

- `ARCHITECTURE.md`: the north-star contract (phases, endpoints, retries/constraints)
- `app/`: FastAPI app (`GET /health` exists; analyze endpoints are planned)
- `core/`: pure utilities (input cleaning, PDF parsing, chunking, Groq client wrapper)
- `agents/`: extractor/verifier orchestration (LangGraph)
- `docs/`: locked HTML architecture + sample payloads
- `tests/`: minimal placeholders for now

