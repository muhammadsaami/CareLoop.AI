"""
CareLoop AI — Models package.
Importing all models here ensures SQLAlchemy's mapper registry
sees every table when Alembic's autogenerate runs.
"""
from app.models.patient import Patient
from app.models.medication import Medication
from app.models.appointment import Appointment, AppointmentStatus
from app.models.warning_symptom import WarningSymptom, SymptomSeverity
from app.models.checkin import CheckIn
from app.models.adherence_log import AdherenceLog
from app.models.discharge_document import (
    DischargeDocument,
    DocumentType,
    ProcessingStatus,
    OcrStatus,
    ExtractionStatus,
)
from app.models.extraction_run import ExtractionRun, ExtractionRunStatus
from app.models.reminder import (
    Reminder,
    ReminderType,
    ReminderStatus,
    Recurrence,
)
from app.models.notification import (
    Notification,
    NotificationType,
    NotificationStatus,
    DeliveryChannel,
)

__all__ = [
    "Patient",
    "Medication",
    "Appointment",
    "AppointmentStatus",
    "WarningSymptom",
    "SymptomSeverity",
    "CheckIn",
    "AdherenceLog",
    "DischargeDocument",
    "DocumentType",
    "ProcessingStatus",
    "OcrStatus",
    "ExtractionStatus",
    "ExtractionRun",
    "ExtractionRunStatus",
    "Reminder",
    "ReminderType",
    "ReminderStatus",
    "Recurrence",
    "Notification",
    "NotificationType",
    "NotificationStatus",
    "DeliveryChannel",
]
