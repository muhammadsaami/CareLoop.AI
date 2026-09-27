"""
CareLoop AI — Reminder Service Tests (Phase 5)

The central safety property of this phase is that a schedule is never invented.
These tests pin that down from several angles:

  * a medication reminder cannot be created without explicit times,
  * the free-text `frequency` is copied for display but never parsed for timing,
  * a schedule that contradicts `frequency` is honoured AND flagged, not
    silently "corrected",
  * an appointment reminder derives its instant from the stored appointment and
    cannot be told a different time,
  * one patient cannot create a reminder against another's medication.

They also cover the ordinary correctness properties: one rule per dose time,
duplicate-slot rejection, and the pause/resume window maths.
"""
from datetime import datetime, time, timedelta, timezone

import pytest

from app.core.exceptions import ReminderNotFoundError, ReminderValidationError
from app.core.timezones import local_naive_to_utc, to_local, utcnow
from app.models.reminder import ReminderStatus, Recurrence
from app.schemas.reminder import (
    AppointmentReminderCreate,
    MedicationReminderCreate,
    ReminderUpdate,
)
from app.services.reminder import ReminderService, implied_daily_count


def _medication_payload(medication, times, **overrides):
    data = {
        "medication_id": medication.id,
        "times": [time.fromisoformat(t) for t in times],
    }
    data.update(overrides)
    return MedicationReminderCreate(**data)


def _as_utc(value):
    """Render a stored timestamp in UTC for an exact assertion.

    Postgres `timestamptz` is returned by the driver in the *session* timezone,
    not UTC - and this project's session timezone is Asia/Calcutta. The instant
    is always correct and aware, so comparisons are sound, but `strftime`
    renders in whatever zone the driver handed back. Converting explicitly keeps
    these assertions about the stored instant rather than about the session
    timezone.
    """
    return value.astimezone(timezone.utc).strftime("%H:%M")


# ── The core safety property ─────────────────────────────────────────────────


class TestMedicationScheduleIsNeverInvented:
    def test_times_are_required(self, db_session, make_patient, make_medication):
        """A medication reminder cannot exist without explicit dose times."""
        patient = make_patient()
        medication = make_medication(patient)

        with pytest.raises(Exception):
            # `times` has no default, so omitting it is a validation error.
            MedicationReminderCreate(medication_id=medication.id)

    def test_frequency_text_is_stored_but_not_used_for_timing(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        """
        The free-text frequency is carried through for display and audit, and
        has no influence on when the reminder fires.
        """
        patient = make_patient()
        medication = make_medication(
            patient, frequency="twice daily with meals"
        )
        service = phase5_services["reminders"]

        reminders = service.create_medication_reminder(
            patient.id,
            _medication_payload(medication, ["09:15"]),
        )

        assert len(reminders) == 1
        assert reminders[0].frequency_text == "twice daily with meals"
        # The supplied time is what scheduled it - not 09:15 derived from
        # "twice daily", and not a default like 08:00.
        assert reminders[0].local_time == time(9, 15)
        local = to_local(
            reminders[0].next_occurrence_at, reminders[0].timezone
        )
        assert local.strftime("%H:%M") == "09:15"

    def test_vague_frequency_produces_no_schedule_and_no_flag(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        """
        "As needed" implies no time, so it yields no signal.

        Crucially the caller must still supply times: vagueness in the source
        data is not permission to guess.
        """
        patient = make_patient()
        medication = make_medication(patient, frequency="as needed for pain")

        assert implied_daily_count("as needed for pain") is None
        assert implied_daily_count(None) is None
        assert implied_daily_count("") is None

        reminders = phase5_services["reminders"].create_medication_reminder(
            patient.id, _medication_payload(medication, ["10:00"])
        )
        assert reminders[0].needs_review is False

    def test_contradiction_is_honoured_and_flagged_not_corrected(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        """
        Three dose times against "once daily".

        The explicit times are what the clinician asked for, so they are used.
        But the contradiction is recorded via `needs_review` so a human checks
        it.  Silently dropping to one time, or silently accepting without
        comment, are both worse.
        """
        patient = make_patient()
        medication = make_medication(patient, frequency="once daily")
        service = phase5_services["reminders"]

        reminders = service.create_medication_reminder(
            patient.id, _medication_payload(medication, ["08:00", "14:00", "20:00"])
        )

        assert len(reminders) == 3
        for reminder in reminders:
            assert reminder.needs_review is True
            assert "implies 1" in reminder.review_reason
        # The supplied schedule is intact.
        assert sorted(r.local_time for r in reminders) == [
            time(8, 0),
            time(14, 0),
            time(20, 0),
        ]

    def test_matching_frequency_needs_no_review(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        patient = make_patient()
        medication = make_medication(patient, frequency="twice daily")
        reminders = phase5_services["reminders"].create_medication_reminder(
            patient.id, _medication_payload(medication, ["08:00", "20:00"])
        )
        assert all(r.needs_review is False for r in reminders)

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("once daily", 1),
            ("Once a day", 1),
            ("OD", 1),
            ("twice daily with meals", 2),
            ("BD", 2),
            ("2x per day", 2),
            ("three times a day", 3),
            ("TDS after food", 3),
            ("QDS", 4),
            ("1 tab TDS", 3),
            # Ambiguous: no count can be derived.
            ("with meals", None),
            ("as needed", None),
            ("at bedtime", None),
            ("every 6 hours if required", None),
        ],
    )
    def test_implied_daily_count_table(self, text, expected):
        assert implied_daily_count(text) == expected


# ── One rule per dose time ───────────────────────────────────────────────────


class TestDoseTimeFanOut:
    def test_creates_one_reminder_per_time(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        patient = make_patient()
        medication = make_medication(patient, frequency="thrice daily")
        reminders = phase5_services["reminders"].create_medication_reminder(
            patient.id, _medication_payload(medication, ["08:00", "14:00", "21:00"])
        )
        assert len(reminders) == 3
        # Independently identifiable and pausable.
        assert len({r.id for r in reminders}) == 3
        assert all(r.medication_id == medication.id for r in reminders)
        assert all(r.reminder_type.value == "medication" for r in reminders)

    def test_duplicate_time_in_one_request_is_rejected(self, db_session, make_patient, make_medication):
        """A repeated slot would double-message the patient."""
        patient = make_patient()
        medication = make_medication(patient)
        with pytest.raises(Exception, match="Duplicate dose time"):
            _medication_payload(medication, ["08:00", "08:00"])

    def test_second_reminder_for_the_same_slot_is_rejected(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        patient = make_patient()
        medication = make_medication(patient)
        service = phase5_services["reminders"]

        service.create_medication_reminder(
            patient.id, _medication_payload(medication, ["08:00"])
        )
        with pytest.raises(ReminderValidationError, match="already exists"):
            service.create_medication_reminder(
                patient.id, _medication_payload(medication, ["08:00"])
            )

    def test_seconds_are_stripped_so_the_same_slot_is_detected(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        """
        08:00:00 and 08:00:30 are the same dose time for scheduling purposes.
        Normalising to the minute means the duplicate check catches it.
        """
        patient = make_patient()
        medication = make_medication(patient)
        service = phase5_services["reminders"]

        service.create_medication_reminder(
            patient.id, _medication_payload(medication, ["08:00"])
        )
        with pytest.raises(ReminderValidationError):
            service.create_medication_reminder(
                patient.id,
                _medication_payload(medication, ["08:00:30"]),
            )

    def test_times_are_sorted_and_returned_deterministically(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        patient = make_patient()
        medication = make_medication(patient, frequency="thrice daily")
        reminders = phase5_services["reminders"].create_medication_reminder(
            patient.id, _medication_payload(medication, ["21:00", "08:00", "14:00"])
        )
        assert [r.local_time for r in reminders] == [
            time(8, 0),
            time(14, 0),
            time(21, 0),
        ]


# ── Timezone handling ────────────────────────────────────────────────────────


class TestReminderTimezones:
    def test_uses_the_patients_timezone_by_default(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        patient = make_patient()
        patient.timezone = "Asia/Kolkata"
        db_session.flush()
        medication = make_medication(patient)

        reminders = phase5_services["reminders"].create_medication_reminder(
            patient.id, _medication_payload(medication, ["08:00"])
        )
        assert reminders[0].timezone == "Asia/Kolkata"
        local = to_local(reminders[0].next_occurrence_at, "Asia/Kolkata")
        assert local.strftime("%H:%M") == "08:00"
        # 08:00 IST is 02:30 UTC.
        assert _as_utc(reminders[0].next_occurrence_at) == "02:30"

    def test_explicit_timezone_overrides_the_patient_default(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        patient = make_patient()
        patient.timezone = "Asia/Kolkata"
        db_session.flush()
        medication = make_medication(patient)

        reminders = phase5_services["reminders"].create_medication_reminder(
            patient.id,
            _medication_payload(medication, ["08:00"], timezone="America/New_York"),
        )
        assert reminders[0].timezone == "America/New_York"
        # Local wall-clock is what the clinician asked for...
        assert (
            to_local(
                reminders[0].next_occurrence_at, "America/New_York"
            ).strftime("%H:%M")
            == "08:00"
        )
        # ...and the stored instant matches that zone's offset FOR THAT DATE.
        # Asserting a fixed UTC hour would be season-dependent (EDT is 12:00Z,
        # EST is 13:00Z), so the expected value is derived, not hardcoded.
        expected_utc = local_naive_to_utc(
            datetime.combine(
                to_local(
                    reminders[0].next_occurrence_at, "America/New_York"
                ).date(),
                time(8, 0),
            ),
            "America/New_York",
        )
        assert reminders[0].next_occurrence_at == expected_utc

    def test_invalid_timezone_is_rejected_at_the_schema(
        self, db_session, make_patient, make_medication
    ):
        patient = make_patient()
        medication = make_medication(patient)
        with pytest.raises(Exception):
            _medication_payload(medication, ["08:00"], timezone="Mars/Phobos")

    def test_naive_window_dates_are_rejected(
        self, db_session, make_patient, make_medication
    ):
        patient = make_patient()
        medication = make_medication(patient)
        with pytest.raises(Exception, match="timezone-aware"):
            _medication_payload(
                medication, ["08:00"], start_date=datetime(2026, 1, 1)
            )

    def test_inverted_window_is_rejected(
        self, db_session, make_patient, make_medication
    ):
        patient = make_patient()
        medication = make_medication(patient)
        with pytest.raises(Exception, match="earlier than"):
            _medication_payload(
                medication,
                ["08:00"],
                start_date=datetime(2026, 2, 1, tzinfo=timezone.utc),
                end_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )

    def test_future_start_date_shifts_the_first_occurrence(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        """A course starting in the future must not fire before it starts."""
        patient = make_patient()
        patient.timezone = "UTC"
        db_session.flush()
        medication = make_medication(patient)

        future = utcnow() + timedelta(days=10)
        reminders = phase5_services["reminders"].create_medication_reminder(
            patient.id,
            _medication_payload(medication, ["08:00"], start_date=future),
        )
        assert reminders[0].next_occurrence_at >= future


# ── Appointment reminders ────────────────────────────────────────────────────


class TestAppointmentReminders:
    def test_derives_the_instant_from_the_stored_appointment(
        self, db_session, make_patient, make_appointment, phase5_services
    ):
        patient = make_patient()
        when = utcnow() + timedelta(days=3)
        appointment = make_appointment(patient, when=when)

        reminder = phase5_services["reminders"].create_appointment_reminder(
            patient.id,
            AppointmentReminderCreate(
                appointment_id=appointment.id, lead_time_minutes=1440
            ),
        )
        assert reminder.appointment_id == appointment.id
        assert reminder.lead_time_minutes == 1440
        assert reminder.recurrence == Recurrence.none
        # Exactly one day before the appointment's own stored instant.
        assert reminder.next_occurrence_at == when - timedelta(minutes=1440)
        assert reminder.appointment_at == when

    def test_request_cannot_override_the_appointment_time(self):
        """
        The schema has no field for the appointment's date/time.

        That absence is the guarantee: a caller cannot point a reminder at a
        different moment than the appointment the reminder is for.
        """
        fields = AppointmentReminderCreate.model_fields
        assert "date" not in fields
        assert "appointment_at" not in fields
        assert "scheduled_for" not in fields
        assert set(fields) == {
            "appointment_id",
            "lead_time_minutes",
            "timezone",
            "notes",
        }

    def test_past_reminder_time_is_rejected(
        self, db_session, make_patient, make_appointment, phase5_services
    ):
        """A reminder that would fire immediately is never what was meant."""
        patient = make_patient()
        appointment = make_appointment(
            patient, when=utcnow() - timedelta(days=1)
        )
        with pytest.raises(ReminderValidationError, match="already in the past"):
            phase5_services["reminders"].create_appointment_reminder(
                patient.id,
                AppointmentReminderCreate(
                    appointment_id=appointment.id, lead_time_minutes=60
                ),
            )

    def test_duplicate_appointment_reminder_is_rejected(
        self, db_session, make_patient, make_appointment, phase5_services
    ):
        patient = make_patient()
        appointment = make_appointment(patient)
        service = phase5_services["reminders"]
        payload = AppointmentReminderCreate(
            appointment_id=appointment.id, lead_time_minutes=60
        )
        service.create_appointment_reminder(patient.id, payload)
        with pytest.raises(ReminderValidationError, match="already exists"):
            service.create_appointment_reminder(patient.id, payload)

    @pytest.mark.parametrize("lead", [0, -60, 60 * 24 * 8])
    def test_lead_time_bounds(self, lead):
        with pytest.raises(Exception):
            AppointmentReminderCreate(
                appointment_id=__import__("uuid").uuid4(), lead_time_minutes=lead
            )

    def test_appointment_of_another_patient_is_not_found(
        self, db_session, make_patient, make_appointment, phase5_services
    ):
        """Cross-patient access is a 404, not a 403: ids cannot be probed."""
        patient_a = make_patient(name="A", suffix="0701")
        patient_b = make_patient(name="B", suffix="0702")
        appointment = make_appointment(patient_b)

        with pytest.raises(ReminderNotFoundError):
            phase5_services["reminders"].create_appointment_reminder(
                patient_a.id,
                AppointmentReminderCreate(
                    appointment_id=appointment.id, lead_time_minutes=60
                ),
            )


# ── Tenancy ──────────────────────────────────────────────────────────────────


class TestOwnership:
    def test_cannot_remind_on_another_patients_medication(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        patient_a = make_patient(name="A", suffix="0801")
        patient_b = make_patient(name="B", suffix="0802")
        medication_b = make_medication(patient_b)

        with pytest.raises(ReminderNotFoundError):
            phase5_services["reminders"].create_medication_reminder(
                patient_a.id, _medication_payload(medication_b, ["08:00"])
            )

    def test_unknown_patient_is_not_found(
        self, db_session, make_medication, phase5_services, make_patient
    ):
        import uuid

        patient = make_patient()
        medication = make_medication(patient)
        with pytest.raises(ReminderNotFoundError):
            phase5_services["reminders"].create_medication_reminder(
                uuid.uuid4(), _medication_payload(medication, ["08:00"])
            )

    def test_unknown_medication_is_not_found(
        self, db_session, make_patient, phase5_services
    ):
        import uuid

        patient = make_patient()
        payload = MedicationReminderCreate(
            medication_id=uuid.uuid4(), times=[time(8, 0)]
        )
        with pytest.raises(ReminderNotFoundError):
            phase5_services["reminders"].create_medication_reminder(
                patient.id, payload
            )


# ── Lifecycle ────────────────────────────────────────────────────────────────


class TestReminderLifecycle:
    def _make(self, phase5_services, make_patient, make_medication, **kwargs):
        patient = make_patient()
        patient.timezone = "UTC"
        phase5_services["db"].flush()
        medication = make_medication(patient, **kwargs)
        reminder = phase5_services["reminders"].create_medication_reminder(
            patient.id, _medication_payload(medication, ["08:00"])
        )[0]
        return patient, reminder

    def test_pause_and_resume(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        _, reminder = self._make(phase5_services, make_patient, make_medication)
        service = phase5_services["reminders"]

        paused = service.update(
            reminder.id, ReminderUpdate(status=ReminderStatus.paused)
        )
        assert paused.status == ReminderStatus.paused

        resumed = service.update(
            reminder.id, ReminderUpdate(status=ReminderStatus.active)
        )
        assert resumed.status == ReminderStatus.active
        # The property that matters: a resumed reminder must never carry a
        # stale instant from before the pause, or the patient would receive a
        # burst of overdue reminders the moment it is resumed.
        assert resumed.next_occurrence_at > utcnow()

    def test_resume_after_a_long_pause_skips_the_missed_occurrences(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        """
        Paused across several days, then resumed: the next occurrence is the
        upcoming one, not a backlog replay.
        """
        _, reminder = self._make(phase5_services, make_patient, make_medication)
        service = phase5_services["reminders"]

        # Actually pause, then age the stored occurrence while paused, so the
        # resume path is the one under test.
        service.update(reminder.id, ReminderUpdate(status=ReminderStatus.paused))
        phase5_services["db"].execute(
            __import__("sqlalchemy").text(
                "UPDATE reminders SET next_occurrence_at = :when WHERE id = :rid"
            ),
            {
                "when": utcnow() - timedelta(days=3),
                "rid": str(reminder.id),
            },
        )
        phase5_services["db"].commit()
        phase5_services["db"].expire_all()

        resumed = service.update(
            reminder.id, ReminderUpdate(status=ReminderStatus.active)
        )
        assert resumed.next_occurrence_at > utcnow()
        # Within a day of now, i.e. tomorrow's 08:00 - not 3 days of backlog.
        assert resumed.next_occurrence_at <= utcnow() + timedelta(days=1)

    def test_cancel_keeps_the_row_for_audit(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        _, reminder = self._make(phase5_services, make_patient, make_medication)
        cancelled = phase5_services["reminders"].cancel(reminder.id)
        assert cancelled.status == ReminderStatus.cancelled
        # Still retrievable: a delivered notification must resolve to its rule.
        assert phase5_services["reminders"].get(reminder.id) is not None

    def test_dose_time_is_not_editable_in_place(self):
        """
        Changing the time of a live rule would silently re-point reminders the
        patient has already been sent, so the update schema does not offer it.
        """
        assert "local_time" not in ReminderUpdate.model_fields
        assert "medication_id" not in ReminderUpdate.model_fields
        assert "appointment_id" not in ReminderUpdate.model_fields
        assert "timezone" not in ReminderUpdate.model_fields

    def test_listing_is_patient_scoped(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        patient_a = make_patient(name="A", suffix="0901")
        patient_b = make_patient(name="B", suffix="0902")
        med_a = make_medication(patient_a)
        med_b = make_medication(patient_b)
        service = phase5_services["reminders"]

        service.create_medication_reminder(
            patient_a.id, _medication_payload(med_a, ["08:00", "20:00"])
        )
        service.create_medication_reminder(
            patient_b.id, _medication_payload(med_b, ["09:00"])
        )

        assert len(service.list_for_patient(patient_a.id)) == 2
        assert len(service.list_for_patient(patient_b.id)) == 1

    def test_listing_can_filter_by_status(
        self, db_session, make_patient, make_medication, phase5_services
    ):
        _, reminder = self._make(phase5_services, make_patient, make_medication)
        service = phase5_services["reminders"]
        service.cancel(reminder.id)

        assert (
            len(
                service.list_for_patient(
                    reminder.patient_id, status=ReminderStatus.cancelled
                )
            )
            == 1
        )
        assert (
            service.list_for_patient(
                reminder.patient_id, status=ReminderStatus.active
            )
            == []
        )
