"""
CareLoop AI — FastAPI Application Entry Point

Phase 1: Backend Foundation
Phase 2: Discharge document ingestion
Phase 3: Grounded RAG retrieval
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.auth_deps import require_authenticated_user
from app.api.routes import (
    adherence,
    agent,
    appointments,
    checkins,
    discharge_documents,
    escalations,
    health,
    medications,
    notifications,
    patients,
    rag,
    reminders,
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
    # Interactive docs and the OpenAPI schema are UNAUTHENTICATED in
    # development, and REMOVED ENTIRELY in production.
    #
    # An open `/openapi.json` is a complete inventory of every endpoint, path
    # parameter, and request field in a clinical API - it hands an attacker the
    # map before they send a single request. Gating it behind a token was
    # considered and rejected: a browser cannot attach an `Authorization` header
    # to a top-level navigation to `/docs`, so a "protected" docs page is
    # either broken for legitimate users or quietly unprotected in practice.
    # Removing the routes has no such failure mode.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
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

    headers: dict[str, str] = {}
    if exc.status_code == 401:
        # RFC 6750 §3: a 401 on a bearer-protected resource MUST carry
        # `WWW-Authenticate: Bearer`. Clients and proxies use it to decide
        # whether to re-authenticate or to give up, and its absence makes a 401
        # indistinguishable from an application-level rejection.
        #
        # `error="invalid_token"` is the RFC's code for a token that was
        # presented and rejected. No reason is given: the client must not be
        # able to tell an expired token from a forged one.
        headers["WWW-Authenticate"] = 'Bearer realm="careloop", error="invalid_token"'

    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": redact_secrets(exc.message, settings=settings)},
        headers=headers,
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

#: Attached to every router except `/health`.
#:
#: Authentication is set here, ONCE PER ROUTER, rather than per route, because
#: per-route attachment is the thing that gets forgotten. A new endpoint added
#: to a protected router is protected automatically, so the failure mode is a
#: route that is visibly public rather than one that is quietly unprotected.
#:
#: Authorization - WHICH PATIENT a request may touch - cannot be done this way.
#: The API names its patient in a path, in a resource id, or in a request body,
#: so that is attached per route. `tests/test_security_routes.py` walks the real
#: route table and fails if any route on these routers is missing it.
PROTECTED = [Depends(require_authenticated_user)]

# ── The public surface, in full ──────────────────────────────────────────────
# `/health` and `/health/ready` are the ONLY unauthenticated routes in the
# application. They are public because a load balancer, a container probe, and
# an uptime monitor must reach them without holding a credential - and because
# what they return is liveness, never patient data: `/health` reports status,
# `/health/ready` reports database and worker reachability. A probe that
# required a token would be a probe that fails precisely when authentication is
# broken, which is the moment an operator needs it to pass.
#
# Nothing else is public. `/docs`, `/redoc` and `/openapi.json` are not
# registered in production at all - see the FastAPI(...) call above.
app.include_router(health.router, prefix=API_V1)

app.include_router(patients.router, prefix=API_V1, dependencies=PROTECTED)
app.include_router(medications.router, prefix=API_V1, dependencies=PROTECTED)
app.include_router(appointments.router, prefix=API_V1, dependencies=PROTECTED)
app.include_router(warning_symptoms.router, prefix=API_V1, dependencies=PROTECTED)
app.include_router(checkins.router, prefix=API_V1, dependencies=PROTECTED)
app.include_router(adherence.router, prefix=API_V1, dependencies=PROTECTED)
app.include_router(discharge_documents.router, prefix=API_V1, dependencies=PROTECTED)
app.include_router(rag.router, prefix=API_V1, dependencies=PROTECTED)
app.include_router(agent.router, prefix=API_V1, dependencies=PROTECTED)
# Phase 5: scheduling & notifications.  `/health/ready` is registered by
# the health router above and needs no prefix beyond API_V1.
app.include_router(reminders.router, prefix=API_V1, dependencies=PROTECTED)
app.include_router(notifications.router, prefix=API_V1, dependencies=PROTECTED)
# Phase 6: daily check-ins & escalation.  Registered under /api/v1 alongside
# the Phase 1 check-in routes, which are unaffected.
app.include_router(escalations.router, prefix=API_V1, dependencies=PROTECTED)
