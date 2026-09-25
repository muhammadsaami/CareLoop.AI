"""
CareLoop AI — Health Route
"""
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    service: str


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description=(
        "Returns a simple health check response. "
        "Does not expose credentials, secrets, or stack traces."
    ),
    tags=["Health"],
)
def health_check() -> HealthResponse:
    """
    Lightweight liveness check.

    Returns ``{"status": "ok", "service": "careloop-ai"}`` when the
    application process is running.  Does NOT verify database connectivity —
    add a /readiness endpoint in a later phase if needed.
    """
    return HealthResponse(status="ok", service="careloop-ai")
