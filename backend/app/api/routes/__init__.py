"""CareLoop AI — API routes package."""
from app.api.routes import (
    health,
    patients,
    medications,
    appointments,
    warning_symptoms,
    checkins,
    adherence,
)

__all__ = [
    "health",
    "patients",
    "medications",
    "appointments",
    "warning_symptoms",
    "checkins",
    "adherence",
]
