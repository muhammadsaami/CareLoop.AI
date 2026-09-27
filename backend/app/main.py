"""
CareLoop AI — FastAPI Application Entry Point

Phase 1: Backend Foundation
Phase 2: Discharge document ingestion
Phase 3: Grounded RAG retrieval
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    adherence,
    agent,
    appointments,
    checkins,
    discharge_documents,
    health,
    medications,
    patients,
    rag,
    warning_symptoms,
)
from app.core.config import get_settings
from app.core.exceptions import CareLoopError
from app.core.logging import configure_logging, get_logger
from app.core.redaction import redact_secrets

# ── Bootstrap ─────────────────────────────────────────────────────────────────

settings = get_settings()
configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: logs startup and shutdown cleanly."""
    logger.info("CareLoop AI starting — environment=%s", settings.environment)
    yield
    logger.info("CareLoop AI shutting down")


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="CareLoop AI",
    description=(
        "Post-hospital-discharge recovery and compliance assistant.\n\n"
        "**Phase 1** — Backend Foundation: patient data, medications, "
        "appointments, warning symptoms, check-ins, and adherence tracking.\n\n"
        "**Phase 2** — Discharge document ingestion: secure PDF/image upload, "
        "text extraction and OCR, and structured LLM extraction of "
        "medications, appointments, and warning symptoms.\n\n"
        "**Phase 3** — Grounded document retrieval: page-aware chunking of "
        "extracted text, ChromaDB vector indexing, and tenant-scoped "
        "retrieval that returns verbatim source excerpts with their page "
        "number. Retrieval only — no answer generation.\n\n"
        "> ⚠️ **Healthcare Safety Notice**: This API stores and retrieves "
        "structured data only. It does not diagnose, recommend treatments, "
        "or determine emergency status. Extracted values are transcriptions "
        "of what a clinician wrote and items flagged `needs_review` require "
        "human confirmation. Phase 3 retrieval returns source text only and "
        "never generates clinical guidance."
    ),
    version="3.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global exception handler ──────────────────────────────────────────────────

@app.exception_handler(CareLoopError)
async def domain_exception_handler(request: Request, exc: CareLoopError) -> JSONResponse:
    """
    Translate a domain exception into a controlled HTTP response.

    Only the client-safe message is returned.  Internal detail, stack
    traces, and provider payloads are logged server-side and never sent to
    the client.
    """
    if exc.status_code >= 500:
        logger.error(
            "Domain error on %s %s: type=%s detail=%s",
            request.method,
            request.url.path,
            type(exc).__name__,
            exc.internal_detail or "-",
        )
    else:
        logger.info(
            "Domain error on %s %s: type=%s status=%s",
            request.method,
            request.url.path,
            type(exc).__name__,
            exc.status_code,
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": redact_secrets(exc.message, settings=settings)},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Catch-all for unhandled exceptions.
    Returns a generic 500 without leaking internal details.
    """
    logger.exception(
        "Unhandled exception on %s %s", request.method, request.url.path
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred. Please try again later."},
    )

# ── Routers ───────────────────────────────────────────────────────────────────

API_V1 = "/api/v1"

app.include_router(health.router, prefix=API_V1)
app.include_router(patients.router, prefix=API_V1)
app.include_router(medications.router, prefix=API_V1)
app.include_router(appointments.router, prefix=API_V1)
app.include_router(warning_symptoms.router, prefix=API_V1)
app.include_router(checkins.router, prefix=API_V1)
app.include_router(adherence.router, prefix=API_V1)
app.include_router(discharge_documents.router, prefix=API_V1)
app.include_router(rag.router, prefix=API_V1)
app.include_router(agent.router, prefix=API_V1)
