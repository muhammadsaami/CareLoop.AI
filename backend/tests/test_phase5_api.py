"""
CareLoop AI — Phase 5 API Tests

Exercises the reminder and notification routes over real HTTP through
`TestClient`, against the real database, with the provider replaced by an
in-memory double so nothing leaves the process.

The emphasis is on the rules that protect a patient rather than on status
codes: a schedule must never be invented from free text, an appointment time
must come from the appointment, one patient's ids must not reach another's
data, and a reminder must never be sent late enough to cause a double dose.
"""
from datetime import datetime, time, timedelta, timezone as dt_timezone

import pytest

from app.core.timezones import utcnow


def _patient(client, name="API Patient", suffix="3001", **kwargs):
    payload = {
        "name": name,
        "date_of_birth": "1980-05-04",
        "gender": "female",
        "contact_number": "+15550100",
        **kwargs,
    }
    response = client.post("/api/v1/patients/", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _medication(client, patient_id, frequency="once a day"):
    response = client.post(
        f"/api/v1/patients/{patient_id}/medications/",
        json={
            "name": "Metformin",
            "dosage": "500mg",
            "frequency": frequency,
            "start_date": "2026-01-01",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _reminder_id(reminders, index=0):
    return reminders[index]["id"]


class TestMedicationReminderCreation:
    def test_creates_one_reminder_per_dose_time(self, client, db_session):
        patient = _patient(client)
        medication = _medication(client, patient["id"])

        response = client.post(
            f"/api/v1/patients/{patient['id']}/reminders/medication",
            json={"medication_id": medication["id"], "times": ["08:00", "20:00"]},
        )

        assert response.status_code == 201, response.text
        body = response.json()
        # Three times a day is three independently pausable rules, not one rule
        # carrying a list - that is what makes a single dose pausable.
        assert len(body) == 2
        assert {r["local_time"] for r in body} == {"08:00:00", "20:00:00"}
        for reminder in body:
            assert reminder["status"] == "active"
            assert reminder["patient_id"] == patient["id"]
            assert reminder["next_occurrence_at"] is not None

    def test_rejects_a_free_text_schedule_field(
        self, client, db_session
    ):
        """
        The server must never interpret prose.  `frequency` already exists on
        the medication as free text; accepting a schedule string here would
        create a second, ambiguous source of truth.

        `times` is included and valid on purpose: without it the request would
        fail for a missing field and the test would pass without ever proving
        the extra field is rejected.
        """
        patient = _patient(client)
        medication = _medication(client, patient["id"])

        response = client.post(
            f"/api/v1/patients/{patient['id']}/reminders/medication",
            json={
                "medication_id": medication["id"],
                "times": ["08:00"],
                "schedule": "morning and evening",
            },
        )

        assert response.status_code == 422
        # And nothing was created behind the rejected request.
        assert client.get(
            f"/api/v1/patients/{patient['id']}/reminders"
        ).json() == []

    def test_rejects_a_frequency_field(self, client, db_session):
        """
        A caller trying to push the free-text frequency through as a schedule
        must be refused, not silently have the field dropped.
        """
        patient = _patient(client, name="Freq Attempt", suffix="3005")
        medication = _medication(client, patient["id"])

        response = client.post(
            f"/api/v1/patients/{patient['id']}/reminders/medication",
            json={
                "medication_id": medication["id"],
                "times": ["08:00"],
                "frequency": "twice daily with meals",
            },
        )

        assert response.status_code == 422

    def test_rejects_times_missing_explicitly(
        self, client, db_session
    ):
        patient = _patient(client)
        medication = _medication(client, patient["id"])

        response = client.post(
            f"/api/v1/patients/{patient['id']}/reminders/medication",
            json={"medication_id": medication["id"]},
        )

        assert response.status_code == 422

    def test_rejects_a_foreign_patients_medication(
        self, client, db_session
    ):
        """
        A medication belonging to another patient must not be attachable, or a
        schedule could be created for someone else's drug.
        """
        patient_a = _patient(client, name="Owner", suffix="3010")
        patient_b = _patient(client, name="Other", suffix="3011")
        medication = _medication(client, patient_a["id"])

        response = client.post(
            f"/api/v1/patients/{patient_b['id']}/reminders/medication",
            json={"medication_id": medication["id"], "times": ["08:00"]},
        )

        assert response.status_code in (404, 422)

    def test_timezone_comes_from_the_patient_not_the_request(
        self, client, db_session
    ):
        """
        The patient owns their timezone.  Accepting one in the request would let
        a caller schedule reminders against a zone the patient is not in.
        """
        patient = _patient(client, timezone="Asia/Kolkata")
        medication = _medication(client, patient["id"])

        response = client.post(
            f"/api/v1/patients/{patient['id']}/reminders/medication",
            json={"medication_id": medication["id"], "times": ["08:00"]},
        )

        assert response.status_code == 201, response.text
        assert response.json()[0]["timezone"] == "Asia/Kolkata"

    def test_local_time_is_preserved_across_timezones(
        self, client, db_session
    ):
        """08:00 Kolkata is 02:30 UTC - the stored instant must reflect that."""
        patient = _patient(client, timezone="Asia/Kolkata")
        medication = _medication(client, patient["id"])

        response = client.post(
            f"/api/v1/patients/{patient['id']}/reminders/medication",
            json={"medication_id": medication["id"], "times": ["08:00"]},
        )

        assert response.status_code == 201, response.text
        reminder = response.json()[0]
        assert reminder["local_time"] == "08:00:00"
        next_at = datetime.fromisoformat(reminder["next_occurrence_at"])
        assert next_at.astimezone(dt_timezone.utc).hour == 2
        assert next_at.astimezone(dt_timezone.utc).minute == 30


class TestAppointmentReminderCreation:
    def _appointment(self, client, patient_id, *, when=None):
        when = when or (utcnow() + timedelta(days=3)).replace(microsecond=0)
        response = client.post(
            f"/api/v1/patients/{patient_id}/appointments/",
            json={
                "appointment_type": "follow_up",
                "doctor_name": "Dr Asha",
                "date": when.isoformat(),
                "clinic_name": "City Clinic",
                "notes": "bring records",
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    def test_appointment_time_comes_from_the_appointment_row(self, client):
        patient = _patient(client, name="Appt Patient", suffix="3020")
        appointment = self._appointment(client, patient["id"])

        response = client.post(
            f"/api/v1/patients/{patient['id']}/reminders/appointment",
            json={"appointment_id": appointment["id"], "lead_time_minutes": 60},
        )

        assert response.status_code == 201, response.text
        reminder = response.json()
        assert reminder["reminder_type"] == "appointment"
        # Exactly one hour before the stored appointment instant.
        appointment_at = datetime.fromisoformat(appointment["date"])
        next_at = datetime.fromisoformat(reminder["next_occurrence_at"])
        assert next_at == appointment_at - timedelta(hours=1)

    def test_request_cannot_override_the_appointment_instant(self, client):
        """
        A second copy of the time in the request is a second source of truth.

        It is rejected rather than ignored.  Silently dropping a `date` the
        caller sent would return 201 and let them believe their override was in
        effect while the reminder fires off the stored appointment instant.
        """
        patient = _patient(client, name="Override", suffix="3021")
        appointment = self._appointment(client, patient["id"])

        response = client.post(
            f"/api/v1/patients/{patient['id']}/reminders/appointment",
            json={
                "appointment_id": appointment["id"],
                "lead_time_minutes": 60,
                "date": "2030-01-01T00:00:00Z",
            },
        )

        assert response.status_code == 422
        # No reminder was created from the rejected request.
        assert client.get(
            f"/api/v1/patients/{patient['id']}/reminders"
        ).json() == []

    def test_valid_request_uses_the_stored_appointment_instant(self, client):
        """The control case: with no override, the row's own instant is used."""
        patient = _patient(client, name="No Override", suffix="3024")
        appointment = self._appointment(client, patient["id"])

        response = client.post(
            f"/api/v1/patients/{patient['id']}/reminders/appointment",
            json={"appointment_id": appointment["id"], "lead_time_minutes": 60},
        )

        assert response.status_code == 201, response.text
        appointment_at = datetime.fromisoformat(appointment["date"])
        next_at = datetime.fromisoformat(response.json()["next_occurrence_at"])
        assert next_at == appointment_at - timedelta(minutes=60)

    def test_past_appointment_is_refused(self, client):
        """A reminder that would fire immediately is never what the caller meant."""
        patient = _patient(client, name="Past Appt", suffix="3022")
        appointment = self._appointment(
            client, patient["id"], when=utcnow() - timedelta(days=1)
        )

        response = client.post(
            f"/api/v1/patients/{patient['id']}/reminders/appointment",
            json={"appointment_id": appointment["id"], "lead_time_minutes": 60},
        )

        assert response.status_code == 422

    def test_absurd_lead_time_is_refused(self, client):
        patient = _patient(client, name="Long Lead", suffix="3023")
        appointment = self._appointment(client, patient["id"])

        response = client.post(
            f"/api/v1/patients/{patient['id']}/reminders/appointment",
            json={
                "appointment_id": appointment["id"],
                "lead_time_minutes": 60 * 24 * 30,
            },
        )

        assert response.status_code == 422


class TestReminderLifecycle:
    def _one_reminder(self, client):
        patient = _patient(client, name="Lifecycle", suffix="3030")
        medication = _medication(client, patient["id"])
        created = client.post(
            f"/api/v1/patients/{patient['id']}/reminders/medication",
            json={"medication_id": medication["id"], "times": ["08:00"]},
        ).json()
        return patient, created[0]

    def test_list_and_filter_by_status(self, client):
        patient, reminder = self._one_reminder(client)

        response = client.get(f"/api/v1/patients/{patient['id']}/reminders")
        assert response.status_code == 200
        assert len(response.json()) == 1

        active = client.get(
            f"/api/v1/patients/{patient['id']}/reminders?status=active"
        )
        assert len(active.json()) == 1

        cancelled = client.get(
            f"/api/v1/patients/{patient['id']}/reminders?status=cancelled"
        )
        assert cancelled.json() == []

    def test_pause_and_resume(self, client):
        patient, reminder = self._one_reminder(client)
        url = f"/api/v1/patients/{patient['id']}/reminders/{reminder['id']}"

        paused = client.patch(url, json={"status": "paused"})
        assert paused.status_code == 200
        assert paused.json()["status"] == "paused"
        # The scheduled instant is deliberately kept: it is the rule's position
        # in its recurrence, and discarding it would make a resumed daily rule
        # jump a day.  Pausing works by filtering on status at scan time, so the
        # instant alone cannot cause a send.

        resumed = client.patch(url, json={"status": "active"})
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "active"
        assert resumed.json()["next_occurrence_at"] is not None

    def test_paused_reminder_is_never_dispatched(
        self, operator_notification_client, db_session
    ):
        """
        The real guarantee behind `paused`.  A paused rule with a due instant
        sitting in the database must produce no notification.
        """
        from sqlalchemy import text

        patient = _patient(operator_notification_client, name="Paused", suffix="3031")
        medication = _medication(operator_notification_client, patient["id"])
        reminder = operator_notification_client.post(
            f"/api/v1/patients/{patient['id']}/reminders/medication",
            json={"medication_id": medication["id"], "times": ["08:00"]},
        ).json()[0]
        when = utcnow() + timedelta(seconds=5)
        db_session.execute(
            text("UPDATE reminders SET next_occurrence_at = :w WHERE id = :r"),
            {"w": when, "r": reminder["id"]},
        )
        db_session.commit()

        paused = operator_notification_client.patch(
            f"/api/v1/patients/{patient['id']}/reminders/{reminder['id']}",
            json={"status": "paused"},
        )
        assert paused.status_code == 200

        result = operator_notification_client.post(
            "/api/v1/notifications/dispatch", json={}
        ).json()
        assert result["scanned"] == 0
        assert result["materialized"] == 0
        assert operator_notification_client.get(
            f"/api/v1/patients/{patient['id']}/notifications"
        ).json()["count"] == 0

    def test_dose_time_cannot_be_edited_in_place(self, client):
        """
        Re-pointing a live rule silently changes what the patient has already
        been told they would receive at.  Cancel and recreate instead.

        The 422 matters as much as the non-change: Pydantic ignores unknown
        fields by default, so without `extra="forbid"` this PATCH would return
        200 and a clinician would believe the new time was in effect.
        """
        patient, reminder = self._one_reminder(client)

        response = client.patch(
            f"/api/v1/patients/{patient['id']}/reminders/{reminder['id']}",
            json={"local_time": "21:30"},
        )

        assert response.status_code == 422
        # The live schedule is untouched.
        assert client.get(
            f"/api/v1/patients/{patient['id']}/reminders/{reminder['id']}"
        ).json()["local_time"] == "08:00:00"

    def test_source_reference_cannot_be_swapped(self, client):
        """Re-pointing a rule at a different medication is equally unsafe."""
        patient, reminder = self._one_reminder(client)
        other_medication = _medication(
            client, patient["id"], frequency="three times a day"
        )

        response = client.patch(
            f"/api/v1/patients/{patient['id']}/reminders/{reminder['id']}",
            json={"medication_id": other_medication["id"]},
        )

        assert response.status_code == 422
        assert client.get(
            f"/api/v1/patients/{patient['id']}/reminders/{reminder['id']}"
        ).json()["medication_id"] == reminder["medication_id"]

    def test_cancel_keeps_the_row_for_audit(self, client):
        patient, reminder = self._one_reminder(client)
        url = f"/api/v1/patients/{patient['id']}/reminders/{reminder['id']}"

        response = client.delete(url)
        assert response.status_code == 204

        # Retained, not deleted: a delivered notification must stay traceable
        # to the rule that produced it.
        fetched = client.get(url)
        assert fetched.status_code == 200
        assert fetched.json()["status"] == "cancelled"


class TestCrossTenantAccess:
    """One patient's ids must never expose another patient's data."""

    def _reminder_for(self, client, suffix):
        patient = _patient(client, name=f"Tenant {suffix}", suffix=suffix)
        medication = _medication(client, patient["id"])
        reminder = client.post(
            f"/api/v1/patients/{patient['id']}/reminders/medication",
            json={"medication_id": medication["id"], "times": ["08:00"]},
        ).json()[0]
        return patient, reminder

    def test_get_reminder_from_another_patient_is_404(self, client):
        owner, reminder = self._reminder_for(client, "3040")
        other, _ = self._reminder_for(client, "3041")

        response = client.get(
            f"/api/v1/patients/{other['id']}/reminders/{reminder['id']}"
        )

        assert response.status_code == 404

    def test_update_reminder_from_another_patient_is_404(self, client):
        owner, reminder = self._reminder_for(client, "3042")
        other, _ = self._reminder_for(client, "3043")

        response = client.patch(
            f"/api/v1/patients/{other['id']}/reminders/{reminder['id']}",
            json={"status": "cancelled"},
        )

        assert response.status_code == 404
        # And it must not have been changed.
        assert client.get(
            f"/api/v1/patients/{owner['id']}/reminders/{reminder['id']}"
        ).json()["status"] == "active"

    def test_cancel_reminder_from_another_patient_is_404(self, client):
        owner, reminder = self._reminder_for(client, "3044")
        other, _ = self._reminder_for(client, "3045")

        response = client.delete(
            f"/api/v1/patients/{other['id']}/reminders/{reminder['id']}"
        )

        assert response.status_code == 404

    def test_reminder_notification_history_is_scoped_to_the_patient(self, client):
        """
        These rows carry the rendered body, so cross-tenant access here is a
        direct disclosure of another patient's medication and contact details.
        """
        owner, reminder = self._reminder_for(client, "3046")
        other, _ = self._reminder_for(client, "3047")

        response = client.get(
            f"/api/v1/patients/{other['id']}"
            f"/reminders/{reminder['id']}/notifications"
        )

        assert response.status_code == 404

    def test_notification_history_is_scoped_per_patient(self, client):
        owner, reminder = self._reminder_for(client, "3048")
        other, _ = self._reminder_for(client, "3049")

        response = client.get(f"/api/v1/patients/{other['id']}/notifications")

        assert response.status_code == 200
        assert response.json()["notifications"] == []


class TestNotificationEndpoints:
    def _due_reminder(self, client, db_session, suffix="3050"):
        from sqlalchemy import text

        patient = _patient(client, name="Notify", suffix=suffix)
        medication = _medication(client, patient["id"])
        reminder = client.post(
            f"/api/v1/patients/{patient['id']}/reminders/medication",
            json={"medication_id": medication["id"], "times": ["08:00"]},
        ).json()[0]
        # Push the occurrence into the dispatch window.
        when = utcnow() + timedelta(seconds=5)
        db_session.execute(
            text("UPDATE reminders SET next_occurrence_at = :w WHERE id = :r"),
            {"w": when, "r": reminder["id"]},
        )
        db_session.commit()
        return patient, reminder

    def test_manual_dispatch_is_idempotent(self, operator_notification_client, db_session):
        patient, reminder = self._due_reminder(operator_notification_client, db_session)

        first = operator_notification_client.post(
            "/api/v1/notifications/dispatch", json={}
        )
        assert first.status_code == 200, first.text
        assert first.json()["materialized"] == 1

        second = operator_notification_client.post(
            "/api/v1/notifications/dispatch", json={}
        )
        assert second.status_code == 200
        assert second.json()["materialized"] == 0

        history = operator_notification_client.get(
            f"/api/v1/patients/{patient['id']}/notifications"
        ).json()
        assert history["count"] == 1

    def test_history_exposes_the_rendered_body(self, operator_notification_client, db_session):
        """
        The history is the record of what the patient was actually told, so the
        body belongs in it.  It must stay free of document and extraction text.
        """
        patient, reminder = self._due_reminder(
            operator_notification_client, db_session, suffix="3051"
        )
        operator_notification_client.post("/api/v1/notifications/dispatch", json={})

        history = operator_notification_client.get(
            f"/api/v1/patients/{patient['id']}/notifications"
        ).json()
        entry = history["notifications"][0]
        assert entry["status"] == "pending"
        assert entry["body"]
        assert entry["patient_id"] == patient["id"]
        # No clinical source text beyond the schedule itself.
        assert "discharge" not in entry["body"].lower()

    def test_history_paginates_and_filters(self, operator_notification_client, db_session):
        patient, reminder = self._due_reminder(
            operator_notification_client, db_session, suffix="3052"
        )
        operator_notification_client.post("/api/v1/notifications/dispatch", json={})

        page = operator_notification_client.get(
            f"/api/v1/patients/{patient['id']}/notifications?skip=0&limit=1"
        ).json()
        assert page["count"] == 1
        assert page["limit"] == 1

        failed = operator_notification_client.get(
            f"/api/v1/patients/{patient['id']}/notifications?status=failed"
        ).json()
        assert failed["notifications"] == []

    def test_retry_endpoint_returns_counts(self, operator_notification_client):
        response = operator_notification_client.post(
            "/api/v1/notifications/retry?limit=10"
        )
        assert response.status_code == 200
        assert set(response.json()) >= {"examined", "sent", "failed"}

    def test_unknown_notification_is_404(self, notification_client):
        import uuid as _uuid

        response = notification_client.get(
            f"/api/v1/notifications/{_uuid.uuid4()}"
        )
        assert response.status_code == 404

    def test_dispatch_rejects_an_absurd_limit(self, operator_notification_client):
        response = operator_notification_client.post(
            "/api/v1/notifications/dispatch", json={"limit": 100000}
        )
        assert response.status_code == 422


class TestPatientTimezoneEndpoint:
    def test_timezone_is_persisted_and_returned(self, client):
        patient = _patient(client, name="TZ", suffix="3060", timezone="Asia/Kolkata")
        assert patient["timezone"] == "Asia/Kolkata"

        fetched = client.get(f"/api/v1/patients/{patient['id']}").json()
        assert fetched["timezone"] == "Asia/Kolkata"

    def test_invalid_timezone_is_a_422_not_a_500(self, client):
        """
        A typo in a timezone is a client mistake.  It must come back as a
        validation error: `InvalidTimezoneError` is a domain exception, not a
        ValueError, so it escapes Pydantic and would otherwise surface as an
        internal server error with no useful field information.
        """
        response = client.post(
            "/api/v1/patients/",
            json={
                "name": "Bad TZ",
                "contact_number": "+15550100",
                "date_of_birth": "1980-05-04",
                "gender": "female",
                "timezone": "Mars/Olympus",
            },
        )
        assert response.status_code == 422
        assert "timezone" in response.text

    def test_timezone_can_be_corrected_by_update(self, client):
        """
        The zone has to be fixable after the fact, or a wrong value would be
        permanent - and it silently shifts every reminder.
        """
        patient = _patient(client, name="Fix TZ", suffix="3061", timezone="UTC")

        response = client.patch(
            f"/api/v1/patients/{patient['id']}", json={"timezone": "Europe/London"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["timezone"] == "Europe/London"

    def test_patient_without_a_timezone_defaults_to_utc(self, client):
        patient = _patient(client, name="Default TZ", suffix="3062")
        assert patient["timezone"] == "UTC"


class TestReadinessEndpoint:
    def test_readiness_does_not_require_redis(self, client):
        """
        There is no broker in the test environment, and readiness must not dial
        Redis: an unreachable broker should surface as a worker-health problem,
        not take the whole API out of rotation.
        """
        response = client.get("/api/v1/health/ready")

        assert response.status_code == 200
        assert "status" in response.json()

    def test_liveness_is_cheap_and_alive(self, client):
        response = client.get("/api/v1/health")
        assert response.status_code == 200
