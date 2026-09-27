"""
CareLoop AI — Health Route

Liveness (/health) and readiness (/health/ready).  They answer different
questions and must not be conflated:

  * Liveness: "is this process running?"  Never touches a dependency, so a
    dependency outage does not cause an orchestrator to kill a healthy process.
  * Readiness: "can this process actually serve requests?"  Verifies the
    database and reports the notification and scheduler wiring.

The split matters most for the reminder worker: an API that is up but cannot
reach Postgres should be pulled from the load balancer, while a worker whose
Redis broker is down should be restarted - but neither should be reported as a
crashed process.
"""
import logging

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import DbSession
from app.core.logging import log_exception_without_phi

logger = logging.getLogger(__name__)

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
    application process is running.  Deliberately does NOT verify database
    connectivity - see ``/health/ready`` for that.
    """
    return HealthResponse(status="ok", service="careloop-ai")


class ReadinessResponse(BaseModel):
    """Readiness detail. Contains capability flags only, never secrets."""

    status: str
    service: str
    database: str
    notification_provider: str
    notification_provider_configured: bool
    scheduler_queue: str
    scheduler_broker_configured: bool


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness check",
    description=(
        "Verifies the database is reachable and reports the notification and "
        "scheduler configuration. Returns 503 when the database is "
        "unavailable. Exposes capability flags only - no URLs, no tokens."
    ),
    tags=["Health"],
)
def readiness_check(db: DbSession) -> ReadinessResponse:
    """
    Verify the process can actually serve requests.

    Redis is NOT dialled here.  A synchronous TCP probe on every readiness call
    would add a broker round trip to a hot endpoint, and the provider/broker
    flags below already tell an operator what is configured.  Worker liveness is
    the worker's own concern.
    """
    from fastapi import Response, status as http_status

    from app.core.config import get_settings
    from app.notifications.factory import build_provider

    settings = get_settings()

    database_status = "ok"
    http_status_code = http_status.HTTP_200_OK
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        # Logged as a type, not a message: a driver error can carry the
        # connection string, which is a credential.
        log_exception_without_phi(logger, "readiness_database_check_failed", exc)
        database_status = "unavailable"
        http_status_code = http_status.HTTP_503_SERVICE_UNAVAILABLE

    try:
        provider = build_provider(settings)
        provider_name = provider.channel
        provider_configured = provider.is_configured()
    except Exception as exc:
        provider_name = "unknown"
        provider_configured = False
        log_exception_without_phi(logger, "readiness_provider_check_failed", exc)

    body = ReadinessResponse(
        status="ready" if database_status == "ok" else "not_ready",
        service="careloop-ai",
        database=database_status,
        # `describe()` would embed the channel name and a boolean; the two
        # explicit fields are clearer and cannot drift into including a URL.
        notification_provider=provider_name,
        notification_provider_configured=provider_configured,
        scheduler_queue=settings.celery_queue,
        scheduler_broker_configured=bool(
            settings.celery_broker_url or settings.redis_url
        ),
    )
    # FastAPI returns 200 by default; set the code explicitly for the failure
    # case so an orchestrator sees 503 rather than a 200 with status=not_ready.
    if http_status_code != http_status.HTTP_200_OK:
        from fastapi.responses import JSONResponse

        return JSONResponse(  # type: ignore[return-value]
            status_code=http_status_code,
            content=body.model_dump(),
        )
    return body
