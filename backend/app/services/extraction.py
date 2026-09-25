"""
CareLoop AI — Structured Extraction Service (Phase 2)

Orchestrates the second half of the ingestion pipeline:

    LLMProvider.extract_structured()
        -> JSON parsing
        -> Pydantic validation (StructuredDischargeExtraction)
        -> safety validation (SafetyValidator)
        -> transactional persistence into Medication / Appointment /
           WarningSymptom

Design constraints:
  - Depends only on the LLMProvider interface, never on a vendor SDK.
  - Persistence of extracted records happens inside ONE transaction.  If
    the write fails, everything is rolled back — there is never partial
    persistence.  The audit row for the failure is written separately so
    the attempt is still recorded.
  - Patients are NEVER created from a document.  The patient_id supplied by
    the API is used as-is.
  - Missing values are never invented.  An extracted item that lacks a
    value the Phase 1 schema requires is SKIPPED and reported, not
    fabricated and not allowed to abort the other items.
  - Warning-symptom severity is a neutral storage label only.  This service
    performs no clinical or risk assessment.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    ExtractionValidationError,
    PersistenceError,
    ProviderNotConfiguredError,
)
from app.core.logging import get_logger
from app.core.prompts import build_extraction_prompt
from app.llm.base import LLMProvider
from app.llm.factory import get_provider
from app.models.discharge_document import DischargeDocument, ExtractionStatus
from app.models.extraction_run import ExtractionRun, ExtractionRunStatus
from app.repositories.appointment import AppointmentRepository
from app.repositories.discharge_document import DischargeDocumentRepository
from app.repositories.medication import MedicationRepository
from app.repositories.warning_symptom import WarningSymptomRepository
from app.schemas.appointment import AppointmentCreate
from app.schemas.extraction import (
    ExtractedAppointment,
    ExtractedMedication,
    ExtractedWarningSymptom,
    ExtractionCounts,
    StructuredDischargeExtraction,
)
from app.schemas.medication import MedicationCreate
from app.schemas.warning_symptom import WarningSymptomCreate
from app.services.safety import SafetyValidator, is_placeholder

logger = get_logger(__name__)

# Name used for the provider JSON schema envelope.
EXTRACTION_SCHEMA_NAME = "discharge_summary_extraction"

# Neutral storage label for extracted warning symptoms.  Phase 1 stores
# warning symptoms as patient-reported records only; no severity is
# inferred from document text.
DEFAULT_WARNING_SEVERITY = "low"

# Date formats accepted for an appointment date WITHOUT inference.
# Only unambiguous machine-parseable forms are honoured; anything else
# keeps the document's own wording in notes for human confirmation.
_ACCEPTED_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d")


class ExtractionService:
    """Runs structured LLM extraction and persists the validated result."""

    def __init__(
        self,
        db: Session,
        *,
        provider: Optional[LLMProvider] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self._db = db
        self._settings = settings or get_settings()
        self._repo = DischargeDocumentRepository(db)
        self._provider = provider
        self._medications = MedicationRepository(db)
        self._appointments = AppointmentRepository(db)
        self._symptoms = WarningSymptomRepository(db)

    # ── Provider access ─────────────────────────────────────────────────────

    @property
    def provider(self) -> LLMProvider:
        """
        Resolve the configured provider lazily.

        Deferred so uploading a document never fails merely because no
        provider is configured; the failure surfaces at the extraction
        stage with a precise, actionable message.
        """
        if self._provider is None:
            self._provider = get_provider(settings=self._settings)
        return self._provider

    # ── Main entry point ────────────────────────────────────────────────────

    def extract_and_persist(
        self,
        *,
        document: DischargeDocument,
        document_text: str,
    ) -> tuple[StructuredDischargeExtraction, ExtractionCounts, dict[str, list[str]]]:
        """
        Extract, validate, and persist.

        Returns ``(extraction, counts, created_ids)`` where ``created_ids``
        maps each category to the UUIDs of the created Phase 1 records.

        Raises a domain exception on failure; the caller decides how to
        report it.  No partial record set is ever left behind.
        """
        provider = self.provider
        provider_name = provider.name
        provider_model = provider.model

        if not provider.is_configured():
            raise ProviderNotConfiguredError(
                f"The '{provider_name}' extraction provider is not "
                "configured. Set its API key in backend/.env to enable "
                "structured extraction. The document was still saved; after "
                "configuring the key, call the reprocess endpoint for this "
                "document instead of uploading it again.",
                internal_detail=f"provider={provider_name} not configured",
            )

        # Audit row first, committed on its own so a later failure can be
        # recorded against it.
        run = self._repo.create_extraction_run(
            document_id=document.id,
            provider=provider_name,
            model=provider_model,
            status=ExtractionRunStatus.STARTED,
            raw_ocr_character_count=len(document_text),
        )
        self._db.commit()

        try:
            extraction = self._run_extraction(
                document_text, page_count=document.page_count
            )
        except Exception as exc:  # noqa: BLE001
            self._record_failure(run, extraction_error=exc, validation_error=None)
            raise

        created_ids = self._persist_with_rollback(
            document.patient_id, extraction, run
        )

        counts = ExtractionCounts(
            medications=len(created_ids["medications"]),
            appointments=len(created_ids["appointments"]),
            warning_symptoms=len(created_ids["warning_symptoms"]),
            needs_review=self._needs_review_count(extraction),
        )

        self._repo.update(
            run,
            status=ExtractionRunStatus.COMPLETED,
            completed_at=_utcnow(),
        )
        self._repo.update(
            document,
            extraction_status=(
                ExtractionStatus.NEEDS_REVIEW
                if counts.needs_review
                else ExtractionStatus.COMPLETED
            ),
            error_message=None,
        )
        self._db.commit()

        logger.info(
            "Extraction completed: document=%s provider=%s model=%s "
            "medications=%s appointments=%s warning_symptoms=%s needs_review=%s",
            document.id,
            provider_name,
            provider_model,
            counts.medications,
            counts.appointments,
            counts.warning_symptoms,
            counts.needs_review,
        )
        return extraction, counts, created_ids

    # ── Stage 1: LLM -> JSON -> Pydantic -> safety ──────────────────────────

    def _run_extraction(
        self, document_text: str, *, page_count: int | None
    ) -> StructuredDischargeExtraction:
        provider = self.provider
        system_prompt, user_prompt = build_extraction_prompt(document_text)
        json_schema = StructuredDischargeExtraction.model_json_schema()

        raw = provider.extract_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            json_schema=json_schema,
            schema_name=EXTRACTION_SCHEMA_NAME,
        )

        # Pydantic validation gate.
        try:
            parsed = StructuredDischargeExtraction.model_validate(raw)
        except ValidationError as exc:
            fields = ", ".join(
                ".".join(str(part) for part in err["loc"]) for err in exc.errors()
            )
            raise ExtractionValidationError(
                "The extraction result did not match the expected schema. "
                "The document can be reprocessed.",
                internal_detail=f"schema validation failed for {fields}"[:500],
            ) from exc

        # Independent safety gate.
        sanitised, notes = SafetyValidator(page_count=page_count).validate(parsed)
        if notes:
            logger.info(
                "Safety validation applied adjustments: count=%s", len(notes)
            )
        return sanitised

    @staticmethod
    def _needs_review_count(extraction: StructuredDischargeExtraction) -> int:
        return (
            sum(1 for m in extraction.medications if m.needs_review)
            + sum(1 for a in extraction.appointments if a.needs_review)
            + sum(1 for w in extraction.warning_symptoms if w.needs_review)
        )

    # ── Stage 2: transactional persistence ──────────────────────────────────

    def _persist_with_rollback(
        self,
        patient_id: uuid.UUID,
        extraction: StructuredDischargeExtraction,
        run: ExtractionRun,
    ) -> dict[str, list[str]]:
        """
        Persist the validated extraction atomically.

        On ANY database error the transaction is rolled back completely, the
        failure is recorded on the audit row, and a domain error is raised.
        No partially written record set survives.
        """
        try:
            created = self._persist(patient_id, extraction)
            self._db.commit()
            return created
        except Exception as exc:  # noqa: BLE001
            self._db.rollback()
            logger.exception(
                "Extraction persistence failed; transaction rolled back "
                "for patient=%s",
                patient_id,
            )
            self._record_failure(run, extraction_error=None, validation_error=exc)
            raise PersistenceError(
                "Failed to save the extraction result. No records were "
                "created; the document can be reprocessed.",
                internal_detail=(
                    f"persistence failed: {exc.__class__.__name__}"
                ),
            ) from exc

    def _persist(
        self, patient_id: uuid.UUID, extraction: StructuredDischargeExtraction
    ) -> dict[str, list[str]]:
        """
        Write extracted items into the Phase 1 tables.

        Items that cannot be stored without inventing a value are skipped
        and counted; they never abort the remaining items and never receive
        a fabricated value.
        """
        created: dict[str, list[str]] = {
            "medications": [],
            "appointments": [],
            "warning_symptoms": [],
        }
        skipped = 0

        for item in extraction.medications:
            payload = self._medication_payload(item)
            if payload is None:
                skipped += 1
                continue
            record = self._medications.create(patient_id, payload)
            created["medications"].append(str(record.id))

        for item in extraction.appointments:
            payload = self._appointment_payload(item)
            if payload is None:
                skipped += 1
                continue
            record = self._appointments.create(patient_id, payload)
            created["appointments"].append(str(record.id))

        for item in extraction.warning_symptoms:
            payload = self._symptom_payload(item)
            if payload is None:
                skipped += 1
                continue
            record = self._symptoms.create(patient_id, payload)
            created["warning_symptoms"].append(str(record.id))

        if skipped:
            logger.warning(
                "Skipped %s extracted item(s) that lacked required values; "
                "values were not invented",
                skipped,
            )
        self._db.flush()
        return created

    # ── Mapping helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _medication_payload(
        item: ExtractedMedication,
    ) -> Optional[MedicationCreate]:
        """
        Map an extracted medication onto MedicationCreate.

        The Phase 1 schema requires name, dosage, and frequency.  When any
        of them is absent the item is SKIPPED (returns None) rather than
        having a value invented for it.
        """
        name = (item.medication_name or "").strip()
        dosage = (item.dosage or "").strip()
        frequency = (item.frequency or "").strip()

        if not name or not dosage or not frequency:
            logger.info(
                "Skipping medication missing a required value "
                "(name/dosage/frequency present=%s/%s/%s, needs_review=%s)",
                bool(name),
                bool(dosage),
                bool(frequency),
                item.needs_review,
            )
            return None

        timing_parts = [
            part for part in (item.timing, item.duration) if part and part.strip()
        ]
        timing = " / ".join(timing_parts) if timing_parts else None

        instructions = item.instructions
        # Preserve traceability alongside the clinical text.
        traceability = ExtractionService._traceability_note(item)
        if traceability:
            instructions = (
                f"{instructions}\n{traceability}" if instructions else traceability
            )

        return MedicationCreate(
            name=name[:255],
            dosage=dosage[:100],
            frequency=frequency[:100],
            timing=timing[:255] if timing else None,
            # start_date / end_date stay null: the document states duration
            # in free text, and parsing it into dates would be an inference.
            start_date=None,
            end_date=None,
            instructions=instructions,
        )

    @staticmethod
    def _appointment_payload(
        item: ExtractedAppointment,
    ) -> Optional[AppointmentCreate]:
        """
        Map an extracted appointment onto AppointmentCreate.

        `doctor_name` is required by the Phase 1 schema, so the document's
        own doctor/department/type label is used.  When the document gives
        none, the item is skipped rather than given an invented name.
        """
        doctor_or_department = (item.doctor_or_department or "").strip()
        appointment_type = (item.appointment_type or "").strip()
        label = doctor_or_department or appointment_type
        if not label:
            logger.info(
                "Skipping appointment with no doctor, department, or type"
            )
            return None

        parsed_date = ExtractionService._parse_date(item.appointment_date)
        location = (item.location or "").strip() or None

        return AppointmentCreate(
            doctor_name=label[:255],
            # Only an unambiguous machine-parseable date is honoured.
            # Otherwise a timestamp placeholder is used and the document's
            # own wording is preserved in notes, flagged for human review.
            date=parsed_date or _utcnow(),
            location=location[:500] if location else None,
            status="scheduled",
            notes=ExtractionService._appointment_notes(item, parsed_date is not None),
        )

    @staticmethod
    def _parse_date(raw: str | None) -> Optional[datetime]:
        """
        Parse an appointment date ONLY when unambiguous.

        Accepts a small set of explicit numeric formats.  Returns None for
        relative or free-form wording ("in 2 weeks", "next month"), which
        must not be guessed at.
        """
        if not raw:
            return None
        text = raw.strip()
        for fmt in _ACCEPTED_DATE_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
                return parsed
            except ValueError:
                continue
        return None

    @staticmethod
    def _appointment_notes(item: ExtractedAppointment, date_was_parsed: bool) -> str:
        """Preserve the document's own appointment wording for review."""
        parts: list[str] = ["REQUIRES REVIEW: extracted from document, not verified"]
        if item.appointment_type:
            parts.append(f"Type: {item.appointment_type}")
        if item.appointment_date:
            marker = "as documented" if not date_was_parsed else "parsed from document"
            parts.append(f"Appointment date {marker}: {item.appointment_date}")
        if item.appointment_time:
            parts.append(f"Time as documented: {item.appointment_time}")
        if item.instructions:
            parts.append(f"Instructions: {item.instructions}")
        if item.source_page:
            parts.append(f"Source page: {item.source_page}")
        if item.source_text:
            parts.append(f"Source text: {item.source_text}")
        if not date_was_parsed and item.appointment_date:
            parts.append(
                "NOTE: the stored timestamp is a placeholder; confirm the "
                "appointment date with the patient before use."
            )
        return "\n".join(parts)[:4000]

    @staticmethod
    def _symptom_payload(
        item: ExtractedWarningSymptom,
    ) -> Optional[WarningSymptomCreate]:
        """
        Map an extracted warning symptom onto WarningSymptomCreate.

        Severity is a fixed neutral label: this service does not assess
        clinical severity, consistent with the Phase 1 safety boundary.
        """
        symptom_text = (item.symptom or item.instruction or "").strip()
        if not symptom_text:
            return None

        detail: list[str] = ["REQUIRES REVIEW: extracted from document, not verified"]
        if item.instruction and item.instruction.strip() != symptom_text:
            detail.append(f"Instruction: {item.instruction}")
        if item.source_page:
            detail.append(f"Source page: {item.source_page}")
        if item.source_text:
            detail.append(f"Source text: {item.source_text}")

        return WarningSymptomCreate(
            description=f"{symptom_text}\n" + "\n".join(detail),
            severity=DEFAULT_WARNING_SEVERITY,
        )

    @staticmethod
    def _traceability_note(item: ExtractedMedication) -> str:
        """Build the source-traceability suffix stored with a medication."""
        parts: list[str] = []
        if item.route:
            parts.append(f"Route: {item.route}")
        if item.source_page:
            parts.append(f"Source page: {item.source_page}")
        if item.source_text:
            parts.append(f"Source text: {item.source_text}")
        if item.needs_review:
            parts.append("NEEDS REVIEW: extracted value is ambiguous")
        return "\n".join(parts)

    # ── Audit helpers ───────────────────────────────────────────────────────

    def _record_failure(
        self,
        run: ExtractionRun,
        *,
        extraction_error: Optional[Exception],
        validation_error: Optional[Exception],
    ) -> None:
        """
        Record a failure on the audit row in its own transaction.

        Errors are reduced to a short client-safe summary.  Stack traces
        and provider payloads are never stored.
        """
        try:
            self._db.rollback()
            self._db.execute(
                ExtractionRun.__table__.update()
                .where(ExtractionRun.id == run.id)
                .values(
                    status=ExtractionRunStatus.FAILED,
                    completed_at=_utcnow(),
                    extraction_error=(
                        _safe_error_summary(extraction_error)
                        if extraction_error
                        else None
                    ),
                    validation_error=(
                        _safe_error_summary(validation_error)
                        if validation_error
                        else None
                    ),
                )
            )
            self._db.commit()
            logger.warning(
                "Extraction run failed: run=%s extraction_error=%s "
                "validation_error=%s",
                run.id,
                extraction_error is not None,
                validation_error is not None,
            )
        except Exception:  # noqa: BLE001 - never mask the original failure
            self._db.rollback()
            logger.exception(
                "Could not record extraction failure for run=%s", run.id
            )


class PersistenceFailure(Exception):
    """
    Retained for backwards compatibility with earlier drafts.

    Persistence failures now surface as `core.exceptions.PersistenceError`
    so the API returns a controlled 500 instead of a raw exception.
    """

    def __init__(self, original: Exception) -> None:
        self.original = original
        super().__init__("Extraction persistence failed")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_error_summary(exc: Exception) -> str:
    """
    Reduce an exception to a short summary safe to store and return.

    Uses the domain exception's client-safe `message` when present.
    Never includes a traceback.
    """
    message = getattr(exc, "message", None) or str(exc) or exc.__class__.__name__
    return f"{exc.__class__.__name__}: {message}"[:500]


# Silence unused-import warnings for names re-exported for convenience.
_ = Any
