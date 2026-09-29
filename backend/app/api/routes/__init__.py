"""CareLoop AI — API routes package."""
from app.api.routes import (
    health,
    auth,
    patients,
    medications,
    appointments,
    warning_symptoms,
    checkins,
    adherence,
)

__all__ = [
    "health",
    "auth",
    "patients",
    "medications",
    "appointments",
    "warning_symptoms",
    "checkins",
    "adherence",
]
