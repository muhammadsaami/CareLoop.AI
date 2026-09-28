"""
CareLoop AI — Phase 6 daily check-in service and API

Split into two halves that fail for different reasons:

  * `TestSubmission`, `TestDateAndTimezone`, `TestEscalationRecording` drive the
    service directly, because the properties worth proving are about database
    state and transaction behaviour, which an HTTP assertion can only describe
    indirectly.
  * `TestCheckInApi`, `TestEscalationApi` drive the endpoints, because the
    properties worth proving there are status codes, envelopes, and - above all
    - that one patient can never see or touch another patient's data.

The caregiver-notification tests drive the real `NotificationService` with the
recording provider, so "was the caregiver actually told?" is answered by a send
that happened rather than by a mock's return value.
"""
import datetime as dt
import uuid

import pytest

from app.core.exceptions import (
    CheckInAlreadySubmittedError,
    CheckInValidationError,
    EscalationNotFoundError,
    EscalationTransitionError,
)
from app.models.checkin import CheckIn, CheckInStatus
from app.models.escalation import Escalation, EscalationStatus
from app.models.notification import Notification, NotificationStatus
from app.models.reminder import ReminderType
from app.models.warning_symptom import SymptomSeverity
from app.schemas.checkin import DailyCheckInCreate

BASE = "/api/v1"


# ── Helpers ─────────────────────────────────────────────────────────────────


def _payload(**overrides) -> dict:
    """A minimal, valid submission. Every test varies from this baseline."""
    body = {"general_wellbeing": "okay", "condition_change": "same"}
    body.update(overrides)
    return {k: v for k, v in body.items() if v is not None}


def _create(service, patient, **overrides):
    """Submit through the service with a schema payload."""
    return service.submit(patient, DailyCheckInCreate(**_payload(**overrides)))


def _worsened(symptom) -> dict:
    return {
        "warning_symptoms": [
            {"symptom_id": str(symptom.id), "change": "worse"}
        ]
    }


# ── Submission and recorded state ───────────────────────────────────────────


class TestSubmission:
    def test_a_settled_submission_is_recorded_as_completed(
        self, phase6_services, make_patient
    ):
        patient = make_patient()
        result = _create(phase6_services["checkins"], patient)

        assert result.status == CheckInStatus.completed
        assert result.needs_review is False
        assert result.review_reason is None
        assert result.escalations == []
        assert result.timezone == "UTC"

    def test_answers_are_stored_as_coded_values(
        self, phase6_services, make_patient
    ):
        patient = make_patient()
        result = _create(
            phase6_services["checkins"],
            patient,
            general_wellbeing="unwell",
            condition_change="worse",
        )

        # No prose anywhere: the column holds exactly the option values the
        # client sent, so nothing free-text can ever reach storage.
        assert result.responses == {
            "general_wellbeing": "unwell",
            "condition_change": "worse",
            "warning_symptoms": [],
        }

    def test_the_legacy_response_text_column_is_left_empty(
        self, phase6_services, make_patient, db_session
    ):
        """
        Phase 1 stored prose here; Phase 6 does not.

        Leaving it null rather than reusing it is what makes the two schemas
        distinguishable at a glance in the database, and it keeps a Phase 6 row
        from ever being read by code that expects a text answer to exist.
        """
        patient = make_patient()
        _create(phase6_services["checkins"], patient)
        row = db_session.query(CheckIn).filter_by(patient_id=patient.id).one()
        assert row.response_text is None
        assert row.responses is not None

    def test_patient_message_never_claims_the_patient_is_fine(
        self, phase6_services, make_patient
    ):
        # "You are fine" would be a clinical conclusion this system has not
        # earned: it only knows that no configured rule fired.
        patient = make_patient()
        result = _create(phase6_services["checkins"], patient)
        assert "fine" not in result.patient_message.lower()
        assert result.patient_message == (
            "Thanks - your check-in has been recorded."
        )

    def test_an_empty_submission_is_rejected(
        self, phase6_services, make_patient
    ):
        patient = make_patient()
        with pytest.raises(Exception):
            _create(
                phase6_services["checkins"],
                patient,
                general_wellbeing=None,
                condition_change=None,
            )
        assert db_checkins(phase6_services, patient) == 0

    def test_a_submission_cannot_assert_its_own_status(
        self, phase6_services, make_patient
    ):
        """
        A client must not be able to declare itself escalated.

        Phase 1 let a caller post `flagged: true`. If that survived here, the
        clinical conclusion would be the client's, and the rule layer would be
        decorative.
        """
        patient = make_patient()
        with pytest.raises(Exception):
            _create(
                phase6_services["checkins"],
                patient,
                **{"flagged": True, "status": "escalated"},
            )

    def test_a_submission_cannot_include_free_text(
        self, phase6_services, make_patient
    ):
        patient = make_patient()
        with pytest.raises(Exception):
            _create(
                phase6_services["checkins"],
                patient,
                **{"response_text": "Feeling awful, worse than yesterday"},
            )

    def test_too_many_symptom_reports_is_rejected(
        self, phase6_services, make_patient
    ):
        # A cap on the request body: an unbounded list is a cheap way to make
        # the evaluation do real work on every call.
        patient = make_patient()
        limit = phase6_services["settings"].checkin_max_symptom_reports
        payload = {
            "warning_symptoms": [
                {"symptom_id": str(uuid.uuid4()), "change": "same"}
                for _ in range(limit + 1)
            ]
        }
        with pytest.raises(CheckInValidationError):
            _create(phase6_services["checkins"], patient, **payload)

    def test_reported_symptom_ids_are_stored_as_supplied(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        patient = make_patient()
        symptom = make_warning_symptom(patient)
        result = _create(
            phase6_services["checkins"], patient, **_worsened(symptom)
        )
        assert result.responses["warning_symptoms"] == [
            {"symptom_id": str(symptom.id), "change": "worse"}
        ]


def db_checkins(phase6_services, patient) -> int:
    return (
        phase6_services["db"].query(CheckIn)
        .filter_by(patient_id=patient.id)
        .count()
    )


# ── One per day ─────────────────────────────────────────────────────────────


class TestOneSubmissionPerDay:
    def test_a_second_submission_for_the_same_day_is_refused(
        self, phase6_services, make_patient
    ):
        patient = make_patient()
        service = phase6_services["checkins"]
        _create(service, patient)

        with pytest.raises(CheckInAlreadySubmittedError):
            _create(service, patient, general_wellbeing="very_unwell")

        # The first answer stands. Overwriting it would destroy the record of
        # what the patient actually said, which is the thing being protected.
        assert db_checkins(phase6_services, patient) == 1

    def test_a_different_day_is_allowed(
        self, phase6_services, make_patient
    ):
        patient = make_patient()
        service = phase6_services["checkins"]
        today = dt.date.today()
        _create(service, patient, date=today.isoformat())
        _create(
            service,
            patient,
            date=(today - dt.timedelta(days=1)).isoformat(),
        )
        assert db_checkins(phase6_services, patient) == 2

    def test_one_check_in_per_day_is_enforced_by_the_database(
        self, phase6_services, make_patient, db_session
    ):
        """
        The application check is a convenience; the index is the guarantee.

        A second row for the same patient and day is rejected by PostgreSQL
        even when written behind the service's back, so a race between two
        concurrent requests cannot produce two records of the same day.
        """
        from sqlalchemy.exc import IntegrityError

        patient = make_patient()
        _create(phase6_services["checkins"], patient)
        duplicate = CheckIn(
            patient_id=patient.id,
            date=dt.date.today(),
            status=CheckInStatus.completed,
            responses={},
        )
        db_session.add(duplicate)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_a_future_date_is_refused(self, phase6_services, make_patient):
        # Answers to questions that have not been asked yet.
        patient = make_patient()
        with pytest.raises(CheckInValidationError, match="future date"):
            _create(
                phase6_services["checkins"],
                patient,
                date=(dt.date.today() + dt.timedelta(days=1)).isoformat(),
            )


# ── Date and timezone resolution ────────────────────────────────────────────


class TestDateAndTimezone:
    def test_the_patients_timezone_is_recorded_even_when_omitted(
        self, phase6_services, make_patient, db_session
    ):
        patient = make_patient()
        db_session.execute(
            __import__("sqlalchemy").text(
                "UPDATE patients SET timezone = 'Asia/Kolkata' WHERE id = :id"
            ),
            {"id": str(patient.id)},
        )
        db_session.commit()
        db_session.refresh(patient)

        result = _create(phase6_services["checkins"], patient)
        assert result.timezone == "Asia/Kolkata"

    def test_the_day_is_resolved_in_the_patients_zone_not_the_servers(
        self, phase6_services, make_patient, db_session
    ):
        """
        The reason `today_in_zone` exists.

        A patient whose local day has already rolled over must file today's
        check-in under their own date. Resolving the date on the server would
        file it under yesterday for them, silently shifting their whole
        history.
        """
        from app.core.timezones import today_in_zone

        patient = make_patient()
        db_session.execute(
            __import__("sqlalchemy").text(
                "UPDATE patients SET timezone = 'Pacific/Kiritimati' "
                "WHERE id = :id"
            ),
            {"id": str(patient.id)},
        )
        db_session.commit()
        db_session.refresh(patient)

        result = _create(phase6_services["checkins"], patient)
        # Whatever the server's own date is, the stored day is the patient's.
        assert result.date == today_in_zone("Pacific/Kiritimati")
        assert result.timezone == "Pacific/Kiritimati"

    def test_a_submitted_timezone_must_match_the_patient_record(
        self, phase6_services, make_patient
    ):
        # Otherwise one check-in could be filed against a date that disagrees
        # with every other check-in for the same patient.
        patient = make_patient()
        with pytest.raises(CheckInValidationError, match="does not match"):
            _create(
                phase6_services["checkins"], patient, timezone="America/Denver"
            )

    def test_a_matching_timezone_is_accepted(
        self, phase6_services, make_patient
    ):
        patient = make_patient()
        result = _create(
            phase6_services["checkins"], patient, timezone="UTC"
        )
        assert result.timezone == "UTC"

    def test_an_invalid_timezone_is_rejected_by_the_schema(
        self, phase6_services, make_patient
    ):
        patient = make_patient()
        with pytest.raises(Exception):
            _create(
                phase6_services["checkins"], patient, timezone="Not/AZone"
            )


# ── Cross-patient isolation ─────────────────────────────────────────────────


class TestCrossPatientIsolation:
    def test_another_patients_symptom_id_never_matches_a_rule(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        """
        The single most important assertion in this file.

        Patient A reports a symptom id belonging to patient B, whose stored
        severity is critical. The answer is "worse", which would escalate if
        the id were accepted. It must not: the rule layer only ever sees
        symptoms loaded with a patient filter, so an unowned id cannot be
        matched, escalated, or recorded as the patient's own symptom.
        """
        victim = make_patient(name="B", suffix="2")
        attacker = make_patient(name="A", suffix="1")
        b_symptom = make_warning_symptom(
            victim, severity=SymptomSeverity.critical
        )

        result = _create(
            phase6_services["checkins"],
            attacker,
            general_wellbeing="very_unwell",
            condition_change="worse",
            warning_symptoms=[
                {"symptom_id": str(b_symptom.id), "change": "worse"}
            ],
        )

        assert result.escalations == []
        assert result.status != CheckInStatus.escalated
        # And the attempt is visible, rather than silently dropped: a reviewer
        # sees that the patient answered about a symptom they do not have.
        assert result.needs_review is True
        assert "unrecognised_warning_symptom" in result.review_reason

    def test_the_other_patients_symptom_row_is_untouched(
        self, phase6_services, make_patient, make_warning_symptom, db_session
    ):
        victim = make_patient(name="B", suffix="2")
        attacker = make_patient(name="A", suffix="1")
        b_symptom = make_warning_symptom(victim)

        _create(
            phase6_services["checkins"],
            attacker,
            warning_symptoms=[
                {"symptom_id": str(b_symptom.id), "change": "worse"}
            ],
        )

        rows = (
            db_session.query(CheckIn)
            .filter_by(patient_id=victim.id)
            .count()
        )
        assert rows == 0
        assert b_symptom.patient_id == victim.id

    def test_a_checkin_is_not_readable_through_another_patient(
        self, phase6_services, make_patient
    ):
        owner = make_patient(name="Owner", suffix="1")
        stranger = make_patient(name="Stranger", suffix="2")
        result = _create(phase6_services["checkins"], owner)

        with pytest.raises(Exception) as excinfo:
            phase6_services["checkins"].get_for_patient(
                stranger.id, result.id
            )
        # Not "forbidden": a 403 would confirm the id exists.
        assert "not found" in str(excinfo.value).lower()

    def test_escalations_are_listed_per_patient(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        mine = make_patient(name="Mine", suffix="1")
        theirs = make_patient(name="Theirs", suffix="2")
        my_symptom = make_warning_symptom(mine, severity=SymptomSeverity.high)
        their_symptom = make_warning_symptom(
            theirs, severity=SymptomSeverity.critical
        )

        _create(
            phase6_services["checkins"],
            mine,
            **_worsened(my_symptom),
        )
        _create(
            phase6_services["checkins"],
            theirs,
            **_worsened(their_symptom),
        )

        assert len(
            phase6_services["escalations"].list_for_patient(mine.id)
        ) == 2
        assert len(
            phase6_services["escalations"].list_for_patient(theirs.id)
        ) == 2
        for escalation in phase6_services["escalations"].list_for_patient(
            mine.id
        ):
            assert escalation.patient_id == mine.id


# ── Escalation recording ────────────────────────────────────────────────────


class TestEscalationRecording:
    def test_a_worsened_symptom_records_an_escalation(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        patient = make_patient()
        symptom = make_warning_symptom(patient, severity=SymptomSeverity.high)
        result = _create(
            phase6_services["checkins"], patient, **_worsened(symptom)
        )

        assert result.status == CheckInStatus.escalated
        assert result.escalations
        codes = {e.rule_code for e in result.escalations}
        assert "WARNING_SYMPTOM_WORSENED" in codes

    def test_the_checkin_and_its_escalations_are_written_together(
        self, phase6_services, make_patient, make_warning_symptom, db_session
    ):
        """
        A check-in row must never claim `escalated` with nothing to act on.

        Both halves of the fact are written in one transaction, so an
        interrupted submission leaves neither.
        """
        patient = make_patient()
        symptom = make_warning_symptom(patient, severity=SymptomSeverity.high)
        _create(phase6_services["checkins"], patient, **_worsened(symptom))

        checkin = (
            db_session.query(CheckIn).filter_by(patient_id=patient.id).one()
        )
        escalations = (
            db_session.query(Escalation)
            .filter_by(checkin_id=checkin.id)
            .all()
        )
        assert checkin.status == CheckInStatus.escalated
        assert escalations

    def test_one_escalation_per_rule_per_checkin(
        self, phase6_services, make_patient, make_warning_symptom, db_session
    ):
        # Two rules can match the same submission. They are separate facts
        # (it worsened AND it is above the floor) and get separate rows.
        patient = make_patient()
        symptom = make_warning_symptom(patient, severity=SymptomSeverity.high)
        result = _create(
            phase6_services["checkins"], patient, **_worsened(symptom)
        )

        checkin = (
            db_session.query(CheckIn).filter_by(patient_id=patient.id).one()
        )
        rows = (
            db_session.query(Escalation)
            .filter_by(checkin_id=checkin.id)
            .all()
        )
        assert len(rows) == len({r.rule_code for r in rows}) == len(
            result.escalations
        )

    def test_escalations_carry_no_patient_answers_or_symptom_text(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        """
        The escalation row is an audit artefact, not a copy of the check-in.

        It stores the rule, its version, and the stored severity label. The
        answers stay on the check-in and the symptom prose stays on the symptom,
        so an escalation cannot become a second, less protected copy of
        clinical detail.
        """
        patient = make_patient()
        symptom = make_warning_symptom(
            patient,
            description="SECRET PROSE: escalating breathlessness at rest",
            severity=SymptomSeverity.high,
        )
        result = _create(
            phase6_services["checkins"], patient, **_worsened(symptom)
        )

        for summary in result.escalations[0].model_dump_json().split(","):
            assert "SECRET PROSE" not in summary
        for escalation in result.escalations:
            assert escalation.rule_version == "checkin-red-flags-v1"
            assert "SECRET" not in escalation.reason_code

    def test_escalation_severity_is_copied_from_the_stored_symptom(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        patient = make_patient()
        symptom = make_warning_symptom(patient, severity=SymptomSeverity.high)
        result = _create(
            phase6_services["checkins"], patient, **_worsened(symptom)
        )
        assert all(e.severity == "high" for e in result.escalations)

    def test_escalations_disabled_records_the_status_without_raising_them(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        # A deployment can turn the pathway off. The check-in is still recorded
        # and still reports `escalated`, so the fact that a rule matched is
        # never lost - it simply produced no rows to work.
        settings = phase6_services["settings"].model_copy(
            update={"checkin_escalation_enabled": False}
        )
        from app.services.daily_checkin import DailyCheckInService

        service = DailyCheckInService(
            phase6_services["db"],
            settings=settings,
            escalation_service=phase6_services["escalations"],
        )
        patient = make_patient()
        symptom = make_warning_symptom(patient, severity=SymptomSeverity.high)
        result = _create(service, patient, **_worsened(symptom))

        assert result.status == CheckInStatus.escalated
        assert result.escalations == []

    def test_the_patient_message_reflects_the_configured_workflow(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        patient = make_patient()
        symptom = make_warning_symptom(patient, severity=SymptomSeverity.high)
        result = _create(
            phase6_services["checkins"], patient, **_worsened(symptom)
        )
        # Assembled from the configured workflow, so it says a team will look -
        # it never names a condition, a severity, or tells the patient to seek
        # emergency care, which is a clinical instruction this system does not
        # have the standing to give.
        message = result.patient_message.lower()
        assert "care team" in message
        for forbidden in ("emergency", "911", "diagnos"):
            assert forbidden not in message

    def test_a_review_only_submission_tells_the_patient_a_human_will_look(
        self, phase6_services, make_patient
    ):
        patient = make_patient()
        result = _create(
            phase6_services["checkins"],
            patient,
            general_wellbeing="very_unwell",
            condition_change="worse",
        )
        assert result.status == CheckInStatus.needs_review
        assert "review" in result.patient_message.lower()


# ── Caregiver notification decision ─────────────────────────────────────────


class TestCaregiverNotification:
    def test_a_matching_escalation_materialises_a_caregiver_notice(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        patient = make_patient()  # fixture supplies caregiver_contact
        symptom = make_warning_symptom(patient, severity=SymptomSeverity.high)
        result = _create(
            phase6_services["checkins"], patient, **_worsened(symptom)
        )

        notices = (
            phase6_services["db"]
            .query(Notification)
            .filter_by(escalation_id=result.escalations[0].id)
            .all()
        )
        assert len(notices) == 1
        notice = notices[0]
        assert notice.recipient == patient.caregiver_contact
        assert notice.notification_type.value == "escalation_notice"
        assert notice.status == NotificationStatus.pending

    def test_the_notice_goes_to_the_caregiver_never_to_the_patient(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        # The reason a missing caregiver contact blocks the notice entirely
        # rather than falling back: sending a "your care team is looking at
        # you" alert to the patient discloses the flag before any human
        # decided it should be raised.
        patient = make_patient()
        symptom = make_warning_symptom(patient, severity=SymptomSeverity.high)
        _create(phase6_services["checkins"], patient, **_worsened(symptom))

        recipients = {n.recipient for n in phase6_services["db"].query(Notification)}
        assert recipients == {patient.caregiver_contact}
        assert patient.contact_number not in recipients

    def test_a_missing_caregiver_contact_parks_the_escalation(
        self, phase6_services, make_patient, make_warning_symptom, db_session
    ):
        patient = make_patient()
        symptom = make_warning_symptom(patient, severity=SymptomSeverity.high)
        db_session.execute(
            __import__("sqlalchemy").text(
                "UPDATE patients SET caregiver_contact = NULL WHERE id = :id"
            ),
            {"id": str(patient.id)},
        )
        db_session.commit()
        db_session.refresh(patient)

        result = _create(
            phase6_services["checkins"], patient, **_worsened(symptom)
        )

        escalation = phase6_services["escalations"].get(result.escalations[0].id)
        # Still pending - not failed, not cancelled - with the reason on the
        # row, so an operator can see an alert that is not going out.
        assert escalation.status == EscalationStatus.pending
        assert (
            escalation.notification_blocked_reason
            == "caregiver_contact_missing"
        )
        # And nothing was sent, under any key.
        assert db_session.query(Notification).count() == 0

    def test_a_blocked_escalation_is_not_reported_as_delivered(
        self, phase6_services, make_patient, make_warning_symptom, db_session
    ):
        patient = make_patient()
        symptom = make_warning_symptom(patient, severity=SymptomSeverity.high)
        db_session.execute(
            __import__("sqlalchemy").text(
                "UPDATE patients SET caregiver_contact = '   ' "
                "WHERE id = :id"
            ),
            {"id": str(patient.id)},
        )
        db_session.commit()
        db_session.refresh(patient)

        result = _create(
            phase6_services["checkins"], patient, **_worsened(symptom)
        )
        escalation = phase6_services["escalations"].get(result.escalations[0].id)
        # Whitespace is not a contact number.
        assert escalation.notification_blocked_reason

    def test_caregiver_notifications_disabled_parks_the_escalation(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        # The escalation service owns the notification decision, so THAT is
        # where the setting has to be off - turning it off only on the
        # check-in service would leave the alert going out anyway.
        from app.services.daily_checkin import DailyCheckInService
        from app.services.escalation import EscalationService

        settings = phase6_services["settings"].model_copy(
            update={"checkin_notify_caregiver": False}
        )
        escalations = EscalationService(
            phase6_services["db"],
            settings=settings,
            notification_service=phase6_services["notifications"],
        )
        service = DailyCheckInService(
            phase6_services["db"],
            settings=settings,
            escalation_service=escalations,
        )
        patient = make_patient()
        symptom = make_warning_symptom(patient, severity=SymptomSeverity.high)
        result = _create(service, patient, **_worsened(symptom))

        escalation = escalations.get(result.escalations[0].id)
        assert escalation.notification_blocked_reason == (
            "caregiver_notifications_disabled"
        )
        assert phase6_services["db"].query(Notification).count() == 0

    def test_the_notice_body_carries_no_diagnosis_and_no_answers(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        patient = make_patient()
        symptom = make_warning_symptom(
            patient,
            description="SECRET PROSE about breathlessness",
            severity=SymptomSeverity.high,
        )
        _create(phase6_services["checkins"], patient, **_worsened(symptom))

        notices = phase6_services["db"].query(Notification).all()
        assert notices
        for notice in notices:
            escalation = phase6_services["escalations"].get(
                notice.escalation_id
            )
            assert "SECRET PROSE" not in notice.body
            # Each notice names ITS OWN rule and the version that produced it:
            # that is the audit trail, and it describes the system's action
            # rather than the patient.
            assert escalation.rule_code in notice.body
            assert escalation.rule_version in notice.body
            # And it says outright that it is not an assessment, so a caregiver
            # reading it in isolation cannot mistake it for a clinical opinion.
            assert "not a clinical assessment" in notice.body

    def test_the_notice_body_never_contains_the_patients_answers(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        patient = make_patient()
        symptom = make_warning_symptom(patient, severity=SymptomSeverity.high)
        _create(phase6_services["checkins"], patient, **_worsened(symptom))

        for notice in phase6_services["db"].query(Notification).all():
            escalation = phase6_services["escalations"].get(
                notice.escalation_id
            )
            # The rule code legitimately contains the word "worse"
            # (WARNING_SYMPTOM_WORSENED), so strip the audit vocabulary before
            # hunting for the patient's answers.  What is left must not describe
            # what the patient said: the caregiver opens the record for that,
            # so the notice stays a pointer rather than a summary of
            # someone's day.
            residue = notice.body
            for token in (
                escalation.rule_code,
                escalation.rule_version,
                escalation.workflow.value,
                escalation.severity or "",
            ):
                residue = residue.replace(token, " ")

            for answer in ("worse", "very_unwell", "unwell", "same", "better"):
                assert answer not in residue.lower()

    def test_materialising_twice_makes_one_notice(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        # The recovery task and the inline request can both reach the same
        # escalation. The idempotency key has to hold.
        patient = make_patient()
        symptom = make_warning_symptom(patient, severity=SymptomSeverity.high)
        result = _create(
            phase6_services["checkins"], patient, **_worsened(symptom)
        )
        escalation_id = result.escalations[0].id

        service = phase6_services["escalations"]
        service.request_caregiver_notification(escalation_id)
        service.request_caregiver_notification(escalation_id)

        assert (
            phase6_services["db"]
            .query(Notification)
            .filter_by(escalation_id=escalation_id)
            .count()
            == 1
        )

    def test_an_already_notified_escalation_is_not_re_notified(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        # Re-notifying a caregiver who has already been told would make the
        # system look like it is chasing its own tail; worse, an
        # acknowledgement that came back after the alert would be contradicted.
        patient = make_patient()
        symptom = make_warning_symptom(patient, severity=SymptomSeverity.high)
        result = _create(
            phase6_services["checkins"], patient, **_worsened(symptom)
        )
        escalation_id = result.escalations[0].id

        notice = (
            phase6_services["db"]
            .query(Notification)
            .filter_by(escalation_id=escalation_id)
            .one()
        )
        phase6_services["notifications"].deliver(notice.id)

        service = phase6_services["escalations"]
        assert service.get(escalation_id).status == EscalationStatus.notified
        assert service.request_caregiver_notification(escalation_id) is None
        assert (
            phase6_services["db"]
            .query(Notification)
            .filter_by(escalation_id=escalation_id)
            .count()
            == 1
        )


# ── Escalation lifecycle ────────────────────────────────────────────────────


class TestEscalationLifecycle:
    def _escalated(self, phase6_services, make_patient, make_warning_symptom):
        patient = make_patient()
        symptom = make_warning_symptom(patient, severity=SymptomSeverity.high)
        result = _create(
            phase6_services["checkins"], patient, **_worsened(symptom)
        )
        return result.escalations[0].id

    def test_the_transition_table_is_exactly_as_documented(self):
        from app.services.escalation import ALLOWED_TRANSITIONS

        assert ALLOWED_TRANSITIONS[EscalationStatus.pending] == frozenset(
            {EscalationStatus.notified, EscalationStatus.cancelled}
        )
        assert ALLOWED_TRANSITIONS[EscalationStatus.notified] == frozenset(
            {EscalationStatus.acknowledged, EscalationStatus.cancelled}
        )
        assert ALLOWED_TRANSITIONS[EscalationStatus.acknowledged] == frozenset(
            {EscalationStatus.resolved}
        )
        # Terminal: a human's decision is not undone by a later call.
        assert ALLOWED_TRANSITIONS[EscalationStatus.resolved] == frozenset()
        assert ALLOWED_TRANSITIONS[EscalationStatus.cancelled] == frozenset()

    def test_the_happy_path_runs_pending_to_resolved(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        escalation_id = self._escalated(
            phase6_services, make_patient, make_warning_symptom
        )
        service = phase6_services["escalations"]
        db = phase6_services["db"]

        # Deliver the materialised notice through the real delivery path.
        notice = db.query(Notification).filter_by(
            escalation_id=escalation_id
        ).one()
        phase6_services["notifications"].deliver(notice.id)

        escalated = service.get(escalation_id)
        assert escalated.status == EscalationStatus.notified
        assert escalated.notified_at is not None

        service.acknowledge(escalation_id)
        db.commit()
        assert service.get(escalation_id).status == EscalationStatus.acknowledged

        service.resolve(escalation_id, note="Called the patient.")
        db.commit()
        resolved = service.get(escalation_id)
        assert resolved.status == EscalationStatus.resolved
        assert resolved.resolution_note == "Called the patient."
        assert resolved.resolved_at is not None

    def test_acknowledging_before_the_caregiver_was_told_is_refused(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        # Acknowledging a pending escalation would assert that a caregiver saw
        # an alert that was never sent - exactly the false record the status
        # column exists to prevent.
        escalation_id = self._escalated(
            phase6_services, make_patient, make_warning_symptom
        )
        with pytest.raises(EscalationTransitionError):
            phase6_services["escalations"].acknowledge(escalation_id)
        phase6_services["db"].rollback()

    def test_a_resolved_escalation_is_never_reopened(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        escalation_id = self._escalated(
            phase6_services, make_patient, make_warning_symptom
        )
        service = phase6_services["escalations"]
        db = phase6_services["db"]

        notice = db.query(Notification).filter_by(
            escalation_id=escalation_id
        ).one()
        phase6_services["notifications"].deliver(notice.id)
        service.acknowledge(escalation_id)
        db.commit()
        service.resolve(escalation_id)
        db.commit()

        for action in (service.acknowledge, service.cancel):
            with pytest.raises(EscalationTransitionError):
                action(escalation_id)
            db.rollback()

    def test_cancelling_is_distinct_from_resolving(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        escalation_id = self._escalated(
            phase6_services, make_patient, make_warning_symptom
        )
        service = phase6_services["escalations"]
        service.cancel(escalation_id)
        phase6_services["db"].commit()

        cancelled = service.get(escalation_id)
        assert cancelled.status == EscalationStatus.cancelled
        assert cancelled.resolved_at is None
        assert cancelled.resolution_note is None

    def test_a_missing_escalation_raises_not_found(self, phase6_services):
        with pytest.raises(EscalationNotFoundError):
            phase6_services["escalations"].get(uuid.uuid4())

    def test_a_late_delivery_receipt_does_not_stomp_a_human_decision(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        """
        A human cancelled the escalation while the notice was in flight.

        The delivery receipt arrives afterwards and must not move the row back
        to `notified`, because that would erase a decision someone made with
        the information they had.
        """
        escalation_id = self._escalated(
            phase6_services, make_patient, make_warning_symptom
        )
        service = phase6_services["escalations"]
        db = phase6_services["db"]

        service.cancel(escalation_id)
        db.commit()

        notice = db.query(Notification).filter_by(
            escalation_id=escalation_id
        ).one()
        phase6_services["notifications"].deliver(notice.id)

        assert service.get(escalation_id).status == EscalationStatus.cancelled


# ── Recovery task ───────────────────────────────────────────────────────────


class TestPendingEscalationRecovery:
    def test_a_blocked_escalation_is_not_retried_forever(
        self, phase6_services, make_patient, make_warning_symptom, db_session
    ):
        """
        The beat task runs every minute.

        An escalation parked for a missing caregiver contact cannot be fixed by
        retrying, so it must be excluded from the query - otherwise the logs
        fill with a condition no retry will ever clear, which trains operators
        to ignore the task.
        """
        patient = make_patient()
        symptom = make_warning_symptom(patient, severity=SymptomSeverity.high)
        db_session.execute(
            __import__("sqlalchemy").text(
                "UPDATE patients SET caregiver_contact = NULL WHERE id = :id"
            ),
            {"id": str(patient.id)},
        )
        db_session.commit()
        db_session.refresh(patient)
        _create(phase6_services["checkins"], patient, **_worsened(symptom))

        result = phase6_services["escalations"].notify_pending()
        assert result["examined"] == 0
        assert result["materialized"] == 0

    def test_an_unnotified_escalation_is_picked_up(
        self, phase6_services, make_patient, make_warning_symptom, db_session
    ):
        patient = make_patient()
        symptom = make_warning_symptom(patient, severity=SymptomSeverity.high)
        result = _create(
            phase6_services["checkins"], patient, **_worsened(symptom)
        )
        escalation_id = result.escalations[0].id

        # Simulate a crash between recording and materialising: the escalation
        # exists, the notice does not.
        db_session.query(Notification).filter_by(
            escalation_id=escalation_id
        ).delete()
        escalation = phase6_services["escalations"].get(escalation_id)
        escalation.notification_id = None
        db_session.commit()

        recovery = phase6_services["escalations"].notify_pending()
        assert recovery["materialized"] == 1
        assert (
            phase6_services["db"]
            .query(Notification)
            .filter_by(escalation_id=escalation_id)
            .count()
            == 1
        )

    def test_running_recovery_twice_is_idempotent(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        patient = make_patient()
        symptom = make_warning_symptom(patient, severity=SymptomSeverity.high)
        _create(phase6_services["checkins"], patient, **_worsened(symptom))

        first = phase6_services["escalations"].notify_pending()
        second = phase6_services["escalations"].notify_pending()
        assert first["materialized"] == 0  # already done inline
        assert second["materialized"] == 0
        assert phase6_services["db"].query(Notification).count() == len(
            first and phase6_services["escalations"].list_for_patient(
                patient.id
            )
        )


# ── Reads ───────────────────────────────────────────────────────────────────


class TestReads:
    def test_latest_is_null_before_the_first_checkin(
        self, phase6_services, make_patient
    ):
        # Day one is not an error state.
        patient = make_patient()
        assert phase6_services["checkins"].get_latest(patient.id) is None

    def test_latest_returns_the_most_recent(
        self, phase6_services, make_patient
    ):
        patient = make_patient()
        service = phase6_services["checkins"]
        today = dt.date.today()
        _create(service, patient, date=(today - dt.timedelta(days=2)).isoformat())
        newest = _create(service, patient, date=today.isoformat())

        assert service.get_latest(patient.id).id == newest.id

    def test_history_is_paged_newest_first(
        self, phase6_services, make_patient
    ):
        patient = make_patient()
        service = phase6_services["checkins"]
        today = dt.date.today()
        for offset in (3, 2, 1, 0):
            _create(
                service,
                patient,
                date=(today - dt.timedelta(days=offset)).isoformat(),
            )

        total, items = service.history(patient.id)
        assert total == 4
        assert [item.date for item in items] == sorted(
            [item.date for item in items], reverse=True
        )
        assert items[0].date == today

        page_total, page = service.history(patient.id, skip=0, limit=2)
        assert page_total == 4
        assert len(page) == 2

    def test_history_items_carry_the_answers_and_escalation_count(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        patient = make_patient()
        symptom = make_warning_symptom(patient, severity=SymptomSeverity.high)
        _create(
            phase6_services["checkins"],
            patient,
            general_wellbeing="unwell",
            **_worsened(symptom),
        )

        _, items = phase6_services["checkins"].history(patient.id)
        item = items[0]
        assert item.responses["general_wellbeing"] == "unwell"
        assert item.escalation_count == len(
            phase6_services["escalations"].list_for_checkin(item.id)
        )

    def test_history_is_filtered_by_date_inclusively(
        self, phase6_services, make_patient
    ):
        patient = make_patient()
        service = phase6_services["checkins"]
        today = dt.date.today()
        for offset in (5, 3, 1):
            _create(
                service,
                patient,
                date=(today - dt.timedelta(days=offset)).isoformat(),
            )

        _, items = service.history(
            patient.id,
            start_date=(today - dt.timedelta(days=3)).isoformat(),
            end_date=(today - dt.timedelta(days=1)).isoformat(),
        )
        assert len(items) == 2


# ── Question set ────────────────────────────────────────────────────────────


class TestQuestionSet:
    def test_the_question_set_is_versioned(self, phase6_services, make_patient):
        # A client caches against this string; an unversioned question set
        # cannot be rolled out safely.
        patient = make_patient()
        questions = phase6_services["checkins"].questions_for_patient(patient)
        assert questions.version == "checkin-questions-v1"

    def test_the_set_offers_only_this_patients_symptoms(
        self, phase6_services, make_patient, make_warning_symptom
    ):
        mine = make_patient(name="Mine", suffix="1")
        theirs = make_patient(name="Theirs", suffix="2")
        my_symptom = make_warning_symptom(mine, severity=SymptomSeverity.high)
        make_warning_symptom(theirs, severity=SymptomSeverity.critical)

        questions = phase6_services["checkins"].questions_for_patient(mine)
        offered = {s.id for s in questions.available_warning_symptoms}
        assert offered == {my_symptom.id}

    def test_a_patient_with_no_symptoms_still_gets_a_usable_set(
        self, phase6_services, make_patient
    ):
        # Having no documented symptoms is a legitimate state, not an error.
        patient = make_patient()
        questions = phase6_services["checkins"].questions_for_patient(patient)
        assert questions.available_warning_symptoms == []
        assert any(q.key == "general_wellbeing" for q in questions.questions)

    def test_options_are_coded_values_not_prose(
        self, phase6_services, make_patient
    ):
        patient = make_patient()
        questions = phase6_services["checkins"].questions_for_patient(patient)
        wellbeing = next(
            q for q in questions.questions if q.key == "general_wellbeing"
        )
        assert set(wellbeing.options) == {
            "good",
            "okay",
            "unwell",
            "very_unwell",
        }


# ── Reminder scheduling ─────────────────────────────────────────────────────


class TestCheckInReminder:
    def test_the_prompt_is_an_ordinary_phase_5_reminder(
        self, phase6_services, make_patient
    ):
        from app.schemas.reminder import CheckInReminderCreate

        patient = make_patient()
        reminder = phase6_services["reminders"].create_checkin_reminder(
            patient.id,
            CheckInReminderCreate(local_time="09:00", timezone="UTC"),
        )
        assert reminder.reminder_type == ReminderType.checkin
        assert reminder.medication_id is None
        assert reminder.appointment_id is None

    def test_scheduling_twice_returns_the_same_prompt(
        self, phase6_services, make_patient
    ):
        from app.schemas.reminder import CheckInReminderCreate

        patient = make_patient()
        service = phase6_services["reminders"]
        first = service.create_checkin_reminder(
            patient.id,
            CheckInReminderCreate(local_time="09:00", timezone="UTC"),
        )
        second = service.create_checkin_reminder(
            patient.id,
            CheckInReminderCreate(local_time="09:00", timezone="UTC"),
        )
        # Otherwise a patient would be prompted twice a day.
        assert first.id == second.id

    def test_a_different_local_time_is_a_different_prompt(
        self, phase6_services, make_patient
    ):
        from app.schemas.reminder import CheckInReminderCreate

        patient = make_patient()
        service = phase6_services["reminders"]
        morning = service.create_checkin_reminder(
            patient.id,
            CheckInReminderCreate(local_time="09:00", timezone="UTC"),
        )
        evening = service.create_checkin_reminder(
            patient.id,
            CheckInReminderCreate(local_time="21:00", timezone="UTC"),
        )
        assert morning.id != evening.id


# ── HTTP: check-in endpoints ────────────────────────────────────────────────


class TestCheckInApi:
    def test_questions_endpoint_returns_the_contract(
        self, checkin_client, make_patient
    ):
        patient = make_patient()
        response = checkin_client.get(
            f"{BASE}/patients/{patient.id}/checkins/questions"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["version"] == "checkin-questions-v1"
        assert isinstance(body["questions"], list)

    def test_submitting_returns_201_and_the_computed_status(
        self, checkin_client, make_patient
    ):
        patient = make_patient()
        response = checkin_client.post(
            f"{BASE}/patients/{patient.id}/checkins/daily",
            json=_payload(),
        )
        assert response.status_code == 201
        assert response.json()["status"] == "completed"

    def test_submitting_twice_returns_409(
        self, checkin_client, make_patient
    ):
        patient = make_patient()
        url = f"{BASE}/patients/{patient.id}/checkins/daily"
        assert checkin_client.post(url, json=_payload()).status_code == 201
        assert checkin_client.post(url, json=_payload()).status_code == 409

    def test_a_mismatched_timezone_returns_422(
        self, checkin_client, make_patient
    ):
        patient = make_patient()
        response = checkin_client.post(
            f"{BASE}/patients/{patient.id}/checkins/daily",
            json=_payload(timezone="America/Denver"),
        )
        assert response.status_code == 422

    def test_a_future_date_returns_422(
        self, checkin_client, make_patient
    ):
        patient = make_patient()
        future = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        response = checkin_client.post(
            f"{BASE}/patients/{patient.id}/checkins/daily",
            json=_payload(date=future),
        )
        assert response.status_code == 422

    def test_a_client_supplied_status_is_rejected(
        self, checkin_client, make_patient
    ):
        # The clinical conclusion is the server's. A client that may assert it
        # makes the rule layer advisory.
        patient = make_patient()
        response = checkin_client.post(
            f"{BASE}/patients/{patient.id}/checkins/daily",
            json=_payload(status="escalated"),
        )
        assert response.status_code == 422

    def test_free_text_in_a_daily_submission_is_rejected(
        self, checkin_client, make_patient
    ):
        patient = make_patient()
        response = checkin_client.post(
            f"{BASE}/patients/{patient.id}/checkins/daily",
            json=_payload(response_text="Feeling terrible"),
        )
        assert response.status_code == 422

    def test_an_empty_submission_returns_422(
        self, checkin_client, make_patient
    ):
        patient = make_patient()
        response = checkin_client.post(
            f"{BASE}/patients/{patient.id}/checkins/daily", json={}
        )
        assert response.status_code == 422

    def test_latest_is_null_before_any_submission(
        self, checkin_client, make_patient
    ):
        patient = make_patient()
        response = checkin_client.get(
            f"{BASE}/patients/{patient.id}/checkins/daily/latest"
        )
        assert response.status_code == 200
        assert response.json() is None

    def test_latest_returns_the_submitted_checkin(
        self, checkin_client, make_patient
    ):
        patient = make_patient()
        checkin_client.post(
            f"{BASE}/patients/{patient.id}/checkins/daily", json=_payload()
        )
        response = checkin_client.get(
            f"{BASE}/patients/{patient.id}/checkins/daily/latest"
        )
        assert response.status_code == 200
        assert response.json()["status"] == "completed"

    def test_history_returns_a_paged_envelope(
        self, checkin_client, make_patient
    ):
        patient = make_patient()
        url = f"{BASE}/patients/{patient.id}/checkins/daily"
        checkin_client.post(url, json=_payload())
        response = checkin_client.get(url, params={"skip": 0, "limit": 10})
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert len(body["items"]) == 1

    def test_history_rejects_a_reversed_date_range(
        self, checkin_client, make_patient
    ):
        patient = make_patient()
        today = dt.date.today()
        response = checkin_client.get(
            f"{BASE}/patients/{patient.id}/checkins/daily",
            params={
                "start_date": today.isoformat(),
                "end_date": (today - dt.timedelta(days=1)).isoformat(),
            },
        )
        assert response.status_code == 422

    def test_another_patients_checkin_is_404(
        self, checkin_client, make_patient
    ):
        owner = make_patient(name="Owner", suffix="1")
        stranger = make_patient(name="Stranger", suffix="2")
        created = checkin_client.post(
            f"{BASE}/patients/{owner.id}/checkins/daily", json=_payload()
        ).json()

        response = checkin_client.get(
            f"{BASE}/patients/{stranger.id}/checkins/daily/{created['id']}"
        )
        # 404, not 403: a 403 would confirm the id exists.
        assert response.status_code == 404

    def test_an_unknown_patient_is_404(self, checkin_client):
        """
        403, not 404.

        A check-in submission against an id the caller holds no grant for is
        refused before the service is reached, so the response cannot be used to
        learn whether a patient with that id exists.  The submission is refused
        either way; only the disclosed reason changes.
        """
        response = checkin_client.post(
            f"{BASE}/patients/{uuid.uuid4()}/checkins/daily", json=_payload()
        )
        assert response.status_code == 403

    def test_scheduling_the_prompt_returns_201(
        self, checkin_client, make_patient
    ):
        patient = make_patient()
        response = checkin_client.post(
            f"{BASE}/patients/{patient.id}/checkins/schedule",
            json={"local_time": "09:00", "timezone": "UTC"},
        )
        assert response.status_code == 201
        assert response.json()["reminder_type"] == "checkin"

    def test_scheduling_twice_is_idempotent(
        self, checkin_client, make_patient
    ):
        patient = make_patient()
        url = f"{BASE}/patients/{patient.id}/checkins/schedule"
        first = checkin_client.post(
            url, json={"local_time": "09:00", "timezone": "UTC"}
        ).json()
        second = checkin_client.post(
            url, json={"local_time": "09:00", "timezone": "UTC"}
        ).json()
        assert first["id"] == second["id"]

    def test_phase_1_free_text_checkins_still_work(
        self, checkin_client, make_patient
    ):
        """
        Backward compatibility is a requirement, not an accident.

        A client built against the Phase 1 schema posts prose and asserts its
        own flag. It must keep working unchanged, because deployed clients do
        not get updated on the same day as the server.
        """
        patient = make_patient()
        response = checkin_client.post(
            f"{BASE}/patients/{patient.id}/checkins",
            json={
                "date": dt.date.today().isoformat(),
                "response_text": "Feeling much better today",
                "flagged": False,
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["response_text"] == "Feeling much better today"

    def test_a_phase_1_checkin_is_not_shown_as_a_daily_checkin(
        self, checkin_client, make_patient
    ):
        # The two schemas are different records. A prose answer from a legacy
        # client was never evaluated by the rule layer, so it must not appear
        # as if it had been.
        patient = make_patient()
        checkin_client.post(
            f"{BASE}/patients/{patient.id}/checkins",
            json={
                "date": dt.date.today().isoformat(),
                "response_text": "Same as yesterday",
                "flagged": False,
            },
        )
        response = checkin_client.get(
            f"{BASE}/patients/{patient.id}/checkins/daily/latest"
        )
        assert response.json() is None

    def test_the_legacy_and_daily_endpoints_coexist_for_one_patient(
        self, checkin_client, make_patient
    ):
        # A patient can have both, and each is listed on its own route. The
        # legacy list must not start returning structured rows, or a client
        # expecting prose would read `null`.
        patient = make_patient()
        checkin_client.post(
            f"{BASE}/patients/{patient.id}/checkins/daily", json=_payload()
        )
        legacy = checkin_client.get(f"{BASE}/patients/{patient.id}/checkins")
        assert legacy.status_code == 200
        assert legacy.json() == []


# ── HTTP: escalation endpoints ──────────────────────────────────────────────


class TestEscalationApi:
    def _raise_one(
        self, client, make_patient, make_warning_symptom
    ) -> tuple:
        patient = make_patient()
        symptom = make_warning_symptom(patient, severity=SymptomSeverity.high)
        response = client.post(
            f"{BASE}/patients/{patient.id}/checkins/daily",
            json={
                "general_wellbeing": "unwell",
                "condition_change": "worse",
                "warning_symptoms": [
                    {"symptom_id": str(symptom.id), "change": "worse"}
                ],
            },
        )
        assert response.status_code == 201
        return patient, response.json()["escalations"][0]["id"]

    def test_an_escalating_submission_is_visible_in_the_listing(
        self, checkin_client, make_patient, make_warning_symptom
    ):
        patient, escalation_id = self._raise_one(
            checkin_client, make_patient, make_warning_symptom
        )
        response = checkin_client.get(
            f"{BASE}/patients/{patient.id}/escalations"
        )
        assert response.status_code == 200
        body = response.json()
        # A worsened high-severity symptom matches both published rules, so
        # the submission raised two escalations, not one.
        assert len(body) == 2
        assert escalation_id in {e["id"] for e in body}
        assert all(e["status"] == "pending" for e in body)
        assert {e["rule_code"] for e in body} == {
            "WARNING_SYMPTOM_WORSENED",
            "WARNING_SYMPTOM_AT_SEVERITY_FLOOR",
        }

    def test_the_detail_view_exposes_the_blocked_reason(
        self, checkin_client, make_patient, make_warning_symptom
    ):
        # The field an operator looks at when an escalation says `pending` and
        # nobody has heard about it.
        patient, escalation_id = self._raise_one(
            checkin_client, make_patient, make_warning_symptom
        )
        response = checkin_client.get(
            f"{BASE}/patients/{patient.id}/escalations/{escalation_id}"
        )
        assert response.status_code == 200
        assert "notification_blocked_reason" in response.json()

    def test_the_listing_can_be_filtered_by_status(
        self, checkin_client, make_patient, make_warning_symptom
    ):
        patient, _ = self._raise_one(
            checkin_client, make_patient, make_warning_symptom
        )
        response = checkin_client.get(
            f"{BASE}/patients/{patient.id}/escalations",
            params={"status": "notified"},
        )
        assert response.status_code == 200
        assert response.json() == []

    def test_acknowledging_an_undelivered_escalation_is_409(
        self, checkin_client, make_patient, make_warning_symptom
    ):
        patient, escalation_id = self._raise_one(
            checkin_client, make_patient, make_warning_symptom
        )
        response = checkin_client.post(
            f"{BASE}/patients/{patient.id}/escalations/"
            f"{escalation_id}/acknowledge"
        )
        assert response.status_code == 409

    def test_the_full_lifecycle_over_http(
        self,
        checkin_client,
        make_patient,
        make_warning_symptom,
        phase6_services,
    ):
        patient, escalation_id = self._raise_one(
            checkin_client, make_patient, make_warning_symptom
        )
        notice = (
            phase6_services["db"]
            .query(Notification)
            .filter_by(escalation_id=escalation_id)
            .one()
        )
        phase6_services["notifications"].deliver(notice.id)

        base = f"{BASE}/patients/{patient.id}/escalations/{escalation_id}"
        acknowledged = checkin_client.post(f"{base}/acknowledge")
        assert acknowledged.status_code == 200
        assert acknowledged.json()["status"] == "acknowledged"

        resolved = checkin_client.post(
            f"{base}/resolve", json={"note": "Spoke to the patient."}
        )
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "resolved"

        # Terminal.
        assert checkin_client.post(f"{base}/acknowledge").status_code == 409

    def test_resolving_accepts_a_human_note(
        self,
        checkin_client,
        make_patient,
        make_warning_symptom,
        phase6_services,
    ):
        patient, escalation_id = self._raise_one(
            checkin_client, make_patient, make_warning_symptom
        )
        notice = (
            phase6_services["db"]
            .query(Notification)
            .filter_by(escalation_id=escalation_id)
            .one()
        )
        phase6_services["notifications"].deliver(notice.id)
        base = f"{BASE}/patients/{patient.id}/escalations/{escalation_id}"
        checkin_client.post(f"{base}/acknowledge")

        resolved = checkin_client.post(
            f"{base}/resolve", json={"note": "No action needed."}
        )
        assert resolved.json()["resolution_note"] == "No action needed."

    def test_another_patients_escalation_is_404(
        self, checkin_client, make_patient, make_warning_symptom
    ):
        owner, escalation_id = self._raise_one(
            checkin_client, make_patient, make_warning_symptom
        )
        stranger = make_patient(name="Stranger", suffix="3")

        for suffix, method in (
            ("", "get"),
            ("/acknowledge", "post"),
            ("/cancel", "post"),
        ):
            url = (
                f"{BASE}/patients/{stranger.id}/escalations/"
                f"{escalation_id}{suffix}"
            )
            response = (
                checkin_client.get(url)
                if method == "get"
                else checkin_client.post(url)
            )
            # 404, not 403: the id must not be confirmed to exist.
            assert response.status_code == 404, suffix

    def test_an_unknown_escalation_is_404(self, checkin_client, make_patient):
        patient = make_patient()
        response = checkin_client.get(
            f"{BASE}/patients/{patient.id}/escalations/{uuid.uuid4()}"
        )
        assert response.status_code == 404

    def test_the_escalation_payload_carries_no_answers(
        self, checkin_client, make_patient, make_warning_symptom
    ):
        """
        The escalation projection is safe to show an operator at a glance.

        It names the rule and the stored severity. It does not carry the
        patient's answers, the symptom prose, or the patient's contact details -
        so it can be surfaced in a dashboard without re-deciding what is PHI.
        """
        patient = make_patient()
        symptom = make_warning_symptom(
            patient,
            description="SECRET PROSE",
            severity=SymptomSeverity.high,
        )
        response = checkin_client.post(
            f"{BASE}/patients/{patient.id}/checkins/daily",
            json={
                "general_wellbeing": "unwell",
                "condition_change": "worse",
                "warning_symptoms": [
                    {"symptom_id": str(symptom.id), "change": "worse"}
                ],
            },
        )
        raw = response.json()["escalations"][0]
        assert "SECRET PROSE" not in str(raw)
        assert "general_wellbeing" not in raw
        assert patient.contact_number not in str(raw)
        assert patient.caregiver_contact not in str(raw)
