"""
CareLoop AI — Reminder Service (Phase 5)

Turns structured data that already exists (a `Medication`, an `Appointment`)
into scheduling rules.

THE CENTRAL SAFETY RULE OF THIS PHASE
A reminder's timing is never guessed.  A `Medication` row carries a free-text
`frequency` such as "twice daily with meals" or "1 tab TDS after food".  That
string contains no clock time, and inferring one - by counting the word "daily",
by assuming 08:00 - would be inventing a clinical schedule.  A patient reminded
at the wrong time takes a dose at the wrong time.

So:
  * Medication reminders require the caller to supply explicit local times.
    `frequency` is copied through for display and audit and is otherwise inert.
  * Appointment reminders derive their instant from `Appointment.date`, which
    already exists, and accept only a lead time.  The appointment's own time is
    never accepted from the request.

Where a supplied schedule *disagrees* with the medication's stated frequency in
an unambiguous way (three daily times against "once daily"), the reminder is
still created but flagged `needs_review`.  Silently dropping a clinician's
explicit instruction to add a review flag is not a safe middle ground: the
scheduling is honoured, and a human is told to check it.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, time, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    ReminderNotFoundError,
    ReminderValidationError,
)
from app.core.timezones import (
    DEFAULT_TIMEZONE,
    ensure_aware,
    local_naive_to_utc,
    next_daily_occurrence,
    next_weekly_occurrence,
    to_local,
    utcnow,
    validate_timezone_name,
)
from app.models.appointment import Appointment
from app.models.medication import Medication
from app.models.patient import Patient
from app.models.reminder import Reminder, ReminderStatus, ReminderType, Recurrence
from app.repositories.reminder import ReminderRepository
from app.schemas.reminder import (
    AppointmentReminderCreate,
    CheckInReminderCreate,
    MedicationReminderCreate,
    ReminderUpdate,
)

logger = logging.getLogger(__name__)

# Counts of doses implied by unambiguous frequency wording.  This is used ONLY
# to detect a contradiction between a clinician's explicit times and the
# medication's own text - never to produce a time.  Ambiguous wording is
# absent from this table on purpose, so "with meals" or "as needed" produces no
# signal and no flag.
_DAILY_DOSE_COUNTS: dict[int, tuple[str, ...]] = {
    1: (
        r"\bonce\s+(?:a\s+|per\s+)?daily\b",
        r"\bonce\s+a\s+day\b",
        r"\bonce\s+per\s+day\b",
        r"\bonce\s+day\b",
        r"\b1\s*x?\s*(?:a\s+|per\s+)?day\b",
        r"\bod\b",
    ),
    2: (
        r"\btwice\s+(?:a\s+|per\s+)?daily\b",
        r"\btwice\s+day\b",
        r"\b2\s*x?\s*(?:a\s+|per\s+)?day\b",
        r"\bbd\b",
    ),
    3: (
        r"\bthrice\s+(?:a\s+|per\s+)?daily\b",
        r"\bthree\s+times\s+(?:a\s+|per\s+)?day\b",
        r"\btds\b",
        r"\b3\s*x?\s*(?:a\s+|per\s+)?day\b",
    ),
    4: (
        r"\bfour\s+times\s+(?:a\s+|per\s+)?day\b",
        r"\bqds\b",
        r"\b4\s*x?\s*(?:a\s+|per\s+)?day\b",
    ),
}


def implied_daily_count(frequency_text: Optional[str]) -> Optional[int]:
    """
    Number of daily doses implied by a frequency string, or None if ambiguous.

    Returns None for anything not in the explicit table, which is the safe
    default: an unreadable string is not evidence of a contradiction.
    """
    if not frequency_text:
        return None
    text = frequency_text.strip().lower()
    for count, patterns in _DAILY_DOSE_COUNTS.items():
        for pattern in patterns:
            if re.search(pattern, text):
                return count
    return None


class ReminderService:
    """Creates, lists, and transitions reminder rules."""

    def __init__(
        self,
        db: Session,
        *,
        settings: Optional[Settings] = None,
    ) -> None:
        self._db = db
        self._repo = ReminderRepository(db)
        # Phase 6 reads `checkin_prompt_time` from here. Injected rather than
        # resolved at import so a test can set the deployment default without
        # touching the environment.
        self._settings = settings or get_settings()

    # ── Medication reminders ────────────────────────────────────────────────
    def create_medication_reminder(
        self,
        patient_id: uuid.UUID,
        data: MedicationReminderCreate,
    ) -> list[Reminder]:
        """
        Create one recurring reminder per explicit dose time.

        A medication with three dose times gets three reminder rules, not one
        rule with three times: each is independently pausable, and a single
        occurrence maps to exactly one notification, which is what makes the
        idempotency key meaningful.

        All of a medication's reminders are created in one transaction so a
        failure on the second time cannot leave the patient with a partial
        schedule.
        """
        patient = self._db.get(Patient, patient_id)
        if patient is None:
            raise ReminderNotFoundError(
                "No patient was found for this reminder."
            )

        medication = self._db.get(Medication, data.medication_id)
        if medication is None or medication.patient_id != patient_id:
            # Same 404 shape as a missing medication: a caller must not be able
            # to discover another patient's medication by probing ids.
            raise ReminderNotFoundError(
                "No medication was found for this patient."
            )

        tz_name = data.timezone or patient.timezone or DEFAULT_TIMEZONE
        # Validate even though the schema did: a patient row written by an
        # earlier phase could carry a bad zone.
        tz_name = validate_timezone_name(tz_name)

        needs_review, review_reason = self._review_medication_schedule(
            medication, data.times
        )

        active_from = (
            ensure_aware(data.start_date, field="start_date")
            if data.start_date
            else None
        )
        active_until = (
            ensure_aware(data.end_date, field="end_date")
            if data.end_date
            else None
        )

        created: list[Reminder] = []
        for dose_time in data.times:
            # Reject a duplicate slot up front so the caller gets a clean 409
            # rather than an IntegrityError from the unique index.
            existing = self._repo.get_by_medication_and_local_time(
                medication.id, dose_time
            )
            if existing is not None:
                raise ReminderValidationError(
                    f"A reminder for this medication at "
                    f"{dose_time.strftime('%H:%M')} already exists "
                    f"(status: {existing.status.value}). Update or cancel it "
                    "instead of creating a second one."
                )

            next_occurrence = self._first_occurrence(
                tz_name=tz_name,
                dose_time=dose_time,
                recurrence=data.recurrence,
                interval=data.recurrence_interval,
                active_from=active_from,
                active_until=active_until,
            )

            reminder = Reminder(
                patient_id=patient_id,
                reminder_type=ReminderType.medication,
                medication_id=medication.id,
                appointment_id=None,
                local_time=dose_time,
                recurrence=data.recurrence,
                recurrence_interval=data.recurrence_interval,
                # Copied for display/audit only. Never parsed to decide timing.
                frequency_text=medication.frequency,
                timezone=tz_name,
                next_occurrence_at=next_occurrence,
                active_from=active_from,
                active_until=active_until,
                status=ReminderStatus.active,
                needs_review=needs_review,
                review_reason=review_reason,
                notes=data.notes,
            )
            created.append(self._repo.create(reminder))

        if needs_review:
            # Logged with ids only - the frequency string is clinical text.
            logger.info(
                "reminder_needs_review patient_id=%s medication_id=%s "
                "reason=%s time_count=%d",
                patient_id,
                medication.id,
                review_reason,
                len(data.times),
            )

        self._db.commit()
        for reminder in created:
            self._db.refresh(reminder)
        return created

    def _review_medication_schedule(
        self,
        medication: Medication,
        times: list[time],
    ) -> tuple[bool, Optional[str]]:
        """
        Detect an unambiguous conflict between dose times and `frequency`.

        The reminder is always created.  This only decides whether to ask a
        human to look at it - it never changes the schedule.
        """
        implied = implied_daily_count(medication.frequency)
        if implied is None:
            # Ambiguous or absent frequency text: no signal, no flag.
            return False, None
        if implied == len(times):
            return False, None
        return True, (
            f"{len(times)} daily dose time(s) were supplied but the "
            f"medication's recorded frequency implies {implied}. The schedule "
            "was used as supplied; please confirm it is correct."
        )

    # ── Appointment reminders ───────────────────────────────────────────────
    def create_appointment_reminder(
        self,
        patient_id: uuid.UUID,
        data: AppointmentReminderCreate,
    ) -> Reminder:
        """
        Create a one-shot reminder derived from an existing appointment.

        The reminder fires at `Appointment.date - lead_time_minutes`.  The
        appointment's own instant is read from the database; the request cannot
        override it.
        """
        patient = self._db.get(Patient, patient_id)
        if patient is None:
            raise ReminderNotFoundError(
                "No patient was found for this reminder."
            )

        appointment = self._db.get(Appointment, data.appointment_id)
        if appointment is None or appointment.patient_id != patient_id:
            raise ReminderNotFoundError(
                "No appointment was found for this patient."
            )

        appointment_at = ensure_aware(
            appointment.date, field="appointment.date"
        )
        reminder_at = appointment_at - timedelta(minutes=data.lead_time_minutes)

        # A reminder time in the past would fire immediately, which is never
        # what the caller meant.  Creating it anyway would put a stale message
        # in the patient's history.
        if reminder_at <= utcnow():
            raise ReminderValidationError(
                "The requested reminder time is already in the past. "
                "Choose a shorter lead time so the reminder falls before the "
                "appointment."
            )

        existing = self._repo.list_for_appointment(appointment.id)
        if existing:
            raise ReminderValidationError(
                "A reminder already exists for this appointment. Update the "
                "existing reminder instead of creating a second one."
            )

        tz_name = validate_timezone_name(
            data.timezone or patient.timezone or DEFAULT_TIMEZONE
        )

        reminder = Reminder(
            patient_id=patient_id,
            reminder_type=ReminderType.appointment,
            medication_id=None,
            appointment_id=appointment.id,
            local_time=None,
            recurrence=Recurrence.none,
            recurrence_interval=1,
            frequency_text=None,
            appointment_at=appointment_at,
            lead_time_minutes=data.lead_time_minutes,
            timezone=tz_name,
            next_occurrence_at=reminder_at,
            active_from=None,
            active_until=appointment_at,
            status=ReminderStatus.active,
            needs_review=False,
            review_reason=None,
            notes=data.notes,
        )
        created = self._repo.create(reminder)
        self._db.commit()
        self._db.refresh(created)
        logger.info(
            "appointment_reminder_created patient_id=%s appointment_id=%s "
            "lead_minutes=%d",
            patient_id,
            appointment.id,
            data.lead_time_minutes,
        )
        return created

    # ── Reads and updates ───────────────────────────────────────────────────
    def get(self, reminder_id: uuid.UUID) -> Reminder:
        reminder = self._repo.get_by_id(reminder_id)
        if reminder is None:
            raise ReminderNotFoundError()
        return reminder

    def list_for_patient(
        self,
        patient_id: uuid.UUID,
        *,
        skip: int = 0,
        limit: int = 100,
        status: Optional[ReminderStatus] = None,
    ) -> list[Reminder]:
        return self._repo.list_for_patient(
            patient_id, skip=skip, limit=limit, status=status
        )

    def update(
        self,
        reminder_id: uuid.UUID,
        data: ReminderUpdate,
    ) -> Reminder:
        """
        Apply a lifecycle or window change.

        Resuming a paused reminder recomputes `next_occurrence_at`: while
        paused the rule stops advancing, so the stored value is stale, and
        firing at it would send a burst of overdue reminders.
        """
        reminder = self.get(reminder_id)
        fields: dict[str, object] = {}

        for key, value in data.model_dump(exclude_unset=True).items():
            fields[key] = value

        if (
            data.status == ReminderStatus.active
            and reminder.status != ReminderStatus.active
        ):
            fields["next_occurrence_at"] = self._recompute_next_occurrence(
                reminder, from_now=True
            )

        if "start_date" not in fields and "end_date" not in fields:
            pass
        else:
            new_from = fields.get("start_date", reminder.active_from)
            new_until = fields.get("end_date", reminder.active_until)
            if (
                isinstance(new_from, datetime)
                and isinstance(new_until, datetime)
                and new_from >= new_until
            ):
                raise ReminderValidationError(
                    "start_date must be earlier than end_date."
                )

        updated = self._repo.update(reminder, **fields)
        self._db.commit()
        self._db.refresh(updated)
        return updated

    def cancel(self, reminder_id: uuid.UUID) -> Reminder:
        """
        Cancel a reminder.

        The row is kept, not deleted: a notification already delivered must
        always resolve back to the rule that produced it.
        """
        reminder = self.get(reminder_id)
        updated = self._repo.update(
            reminder, status=ReminderStatus.cancelled
        )
        self._db.commit()
        self._db.refresh(updated)
        return updated

    # ── Phase 6: the daily check-in prompt ──────────────────────────────────
    def create_checkin_reminder(
        self,
        patient_id: uuid.UUID,
        data: CheckInReminderCreate,
    ) -> Reminder:
        """
        Create the daily check-in prompt, or return the one already there.

        This is a Phase 5 `Reminder` with `reminder_type = checkin`, so the
        existing due-scan materialises it, the existing delivery worker sends
        it, and the existing retry policy covers a failed send.  Phase 6 adds
        no scheduler of its own.

        IDEMPOTENT BY DESIGN
        Creating a second daily prompt would make the patient receive two
        identical messages a day, so the call is safe to repeat: an existing
        prompt for the same patient and local time is returned as-is.  A
        caller who wants a different time cancels this one and creates another
        - an explicit act, rather than an accidental duplicate.

        The message content is fixed and non-clinical: it says a daily
        check-in is being requested and nothing about the patient's condition.
        `NotificationService` renders it, and the escalation rules decide
        whether anything about the ANSWERS needs attention - never the prompt.
        """
        patient = self._db.get(Patient, patient_id)
        if patient is None:
            raise ReminderNotFoundError(
                "No patient was found for this reminder."
            )

        tz_name = validate_timezone_name(
            data.timezone or patient.timezone or DEFAULT_TIMEZONE
        )
        prompt_time = data.local_time or self._settings.checkin_prompt_time

        existing = self._repo.get_checkin_slot(patient_id, prompt_time)
        if existing is not None:
            logger.info(
                "checkin_reminder_reused patient_id=%s reminder_id=%s",
                patient_id,
                existing.id,
            )
            return existing

        next_occurrence = self._first_occurrence(
            tz_name=tz_name,
            dose_time=prompt_time,
            recurrence=Recurrence.daily,
            interval=1,
            active_from=None,
            active_until=None,
        )

        reminder = Reminder(
            patient_id=patient_id,
            reminder_type=ReminderType.checkin,
            medication_id=None,
            appointment_id=None,
            local_time=prompt_time,
            recurrence=Recurrence.daily,
            recurrence_interval=1,
            frequency_text=None,
            timezone=tz_name,
            next_occurrence_at=next_occurrence,
            active_from=None,
            active_until=None,
            status=ReminderStatus.active,
            # A check-in prompt carries no clinical content, so there is nothing
            # here for a human to review. Review belongs to the ANSWERS, and
            # that flag lives on the check-in row.
            needs_review=False,
            review_reason=None,
            notes=data.notes,
        )
        created = self._repo.create(reminder)
        self._db.commit()
        self._db.refresh(created)
        logger.info(
            "checkin_reminder_created patient_id=%s reminder_id=%s local_time=%s",
            patient_id,
            created.id,
            prompt_time.strftime("%H:%M"),
        )
        return created

    # ── Occurrence maths ────────────────────────────────────────────────────
    def _first_occurrence(
        self,
        *,
        tz_name: str,
        dose_time: time,
        recurrence: Recurrence,
        interval: int,
        active_from: Optional[datetime],
        active_until: Optional[datetime],
    ) -> Optional[datetime]:
        """
        The first instant this rule should fire.

        Derived in the patient's local wall-clock space and then converted, so
        the first dose lands on the intended local clock time rather than at a
        UTC offset's worth of drift from it.
        """
        now = utcnow()

        if recurrence == Recurrence.none:
            local_today = to_local(now, tz_name).date()
            instant = local_naive_to_utc(
                datetime.combine(local_today, dose_time), tz_name
            )
            return self._clamp_to_window(instant, active_from, active_until)

        instant = self._compute_next(
            now, tz_name, dose_time, recurrence, interval
        )
        if active_from and instant < active_from:
            # Jump to the first occurrence at or after the window opens.
            instant = self._compute_next(
                max(active_from, now), tz_name, dose_time, recurrence, interval
            )
        return self._clamp_to_window(instant, active_from, active_until)

    def _compute_next(
        self,
        after: datetime,
        tz_name: str,
        dose_time: time,
        recurrence: Recurrence,
        interval: int,
    ) -> datetime:
        if recurrence == Recurrence.weekly:
            # Anchor the weekday on the instant the rule is created from, so a
            # weekly cadence keeps the same day of week instead of drifting to
            # whatever day the scheduler happened to run on.
            anchor_weekday = to_local(after, tz_name).weekday()
            return next_weekly_occurrence(
                after, tz_name, dose_time, weekday=anchor_weekday, interval=interval
            )
        return next_daily_occurrence(after, tz_name, dose_time)

    def _recompute_next_occurrence(
        self, reminder: Reminder, *, from_now: bool
    ) -> Optional[datetime]:
        if reminder.reminder_type != ReminderType.medication:
            # A one-shot reminder's instant is fixed; re-deriving it would move
            # an appointment reminder away from its appointment.
            return reminder.next_occurrence_at
        if reminder.local_time is None:
            return None
        after = utcnow() if from_now else (reminder.next_occurrence_at or utcnow())
        return self._clamp_to_window(
            self._compute_next(
                after,
                reminder.timezone,
                reminder.local_time,
                reminder.recurrence,
                reminder.recurrence_interval,
            ),
            reminder.active_from,
            reminder.active_until,
        )

    @staticmethod
    def _clamp_to_window(
        instant: datetime,
        active_from: Optional[datetime],
        active_until: Optional[datetime],
    ) -> Optional[datetime]:
        """Return None when the occurrence falls outside the active window."""
        if active_from and instant < active_from:
            return None
        if active_until and instant > active_until:
            return None
        return instant
