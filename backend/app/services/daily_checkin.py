"""
CareLoop AI — Daily Check-In Service (Phase 6)

Orchestrates one structured submission: validate, load the patient's own stored
facts, evaluate the published rules, record the result, and hand any escalation
to the escalation service.

THE ORDER OF OPERATIONS IS PART OF THE SAFETY DESIGN
The rule layer runs against the patient's OWN `WarningSymptom` rows, loaded
with a patient filter, and against nothing else.  There is no point in this
service where a symptom id from the request is used to fetch "whatever symptom
that is" - that single line of code is where a cross-patient clinical leak
would live if it existed, so it does not exist.  A submitted id that is not in
the patient's loaded set becomes a review code, and that is all.

WHAT THIS SERVICE DELIBERATELY DOES NOT PRODUCE
No text.  `patient_message` is assembled from the escalation's CONFIGURED
workflow and a fixed set of sentences; see `_patient_message`.  The service
returns a status, a set of rule codes, and a workflow - and the workflow is a
category an operator configured in advance, not advice generated per patient.
"""
from __future__ import annotations

import logging
import uuid
import datetime as dt
from typing import Optional, Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    CheckInAlreadySubmittedError,
    CheckInNotFoundError,
    CheckInValidationError,
)
from app.core.timezones import DEFAULT_TIMEZONE, today_in_zone, utcnow
from app.models.checkin import CheckIn, CheckInStatus
from app.models.escalation import Escalation, EscalationWorkflow
from app.models.patient import Patient
from app.models.warning_symptom import WarningSymptom
from app.repositories.checkin import CheckInRepository
from app.repositories.warning_symptom import WarningSymptomRepository
from app.schemas.checkin import (
    DailyCheckInCreate,
    DailyCheckInHistoryItem,
    DailyCheckInResponse,
    EscalationSummary,
)
from app.services.checkin_questions import build_question_set
from app.services.escalation import EscalationService
from app.services.red_flag_rules import (
    RuleSet,
    SymptomReport,
    build_reports,
)

logger = logging.getLogger(__name__)

# ── Patient-facing messages ───────────────────────────────────────────────
#
# Every sentence a patient can be shown is written out here, in full, with no
# interpolation of clinical content.  That is the enforcement mechanism: a
# caregiver-facing alert may reference a rule, but the PATIENT's copy may not
# carry a rule code, a severity, or a symptom, because there is no code path
# that puts one there.
#
# What these messages deliberately do NOT say:
#   * no condition name, ever - the system does not diagnose;
#   * no severity, and no "this is serious" - it does not assess;
#   * no instruction to seek emergency care - prescribing a clinical response is
#     outside this system's role, and a message that implies urgency it cannot
#     justify is worse than one that routes the patient to their care team.
#
# What they do say: that a configured rule matched, and who is being asked to
# look.  That is true, checkable, and is all the system actually knows.
MESSAGES: dict[EscalationWorkflow, str] = {
    EscalationWorkflow.contact_care_team: (
        "Thanks - some of your answers matched a check we have configured. "
        "Your care team has been asked to review this check-in. If you feel "
        "urgent, contact your care team directly."
    ),
    EscalationWorkflow.review_by_care_team: (
        "Thanks - some of your answers matched a check we have configured. "
        "Your care team has been asked to review this check-in. If you feel "
        "urgent, contact your care team directly."
    ),
}

#: The order in which workflows win when a check-in raised more than one
#: escalation.  "Contact the care team" is the more active workflow of the two,
#: so a patient is told the more active thing rather than the milder one.
#: Ordering is over CONFIGURED workflow values - never over a severity this
#: system computed.
WORKFLOW_PRIORITY: tuple[EscalationWorkflow, ...] = (
    EscalationWorkflow.contact_care_team,
    EscalationWorkflow.review_by_care_team,
)

#: Shown when nothing matched and nothing needed review.  Deliberately NOT
#: "you are fine": what the system knows is that no configured rule fired, and
#: the configured rules are not an assessment of the patient.
MESSAGE_RECORDED = "Thanks - your check-in has been recorded."

#: Shown when a response could not be evaluated with confidence.  Says a human
#: will look, because that is the action `needs_review` actually triggers.
MESSAGE_NEEDS_REVIEW = (
    "Thanks - your check-in has been recorded. One of your answers could not "
    "be checked automatically, so a member of the care team will review it."
)


class DailyCheckInService:
    """Structured daily check-ins, their evaluation, and their history."""

    def __init__(
        self,
        db: Session,
        *,
        settings: Optional[Settings] = None,
        escalation_service: Optional[EscalationService] = None,
    ) -> None:
        self._db = db
        self._repo = CheckInRepository(db)
        self._symptoms = WarningSymptomRepository(db)
        self._settings = settings or get_settings()
        self._escalations = escalation_service or EscalationService(db)

    # ── Submission ──────────────────────────────────────────────────────────
    def submit(
        self,
        patient: Patient,
        data: DailyCheckInCreate,
    ) -> DailyCheckInResponse:
        """
        Record and evaluate one day's structured check-in.

        A single transaction: the check-in row and its escalations either both
        land or neither does.  A check-in with no escalation row but a status of
        `escalated` would be a record that says a clinical rule fired while
        containing nothing to act on, so the two are never written separately.
        """
        checkin_date, tz_name = self._resolve_date_and_timezone(patient, data)
        self._validate_report_count(data)

        existing = self._repo.get_by_patient_and_date(patient.id, checkin_date)
        if existing is not None:
            raise CheckInAlreadySubmittedError()

        owned = self._load_owned_symptoms(patient.id)
        reports, ownership_review_codes = self._build_reports(data, owned)
        evaluation = self._rule_set().evaluate(
            wellbeing=data.general_wellbeing,
            condition_change=data.condition_change,
            reports=reports,
        )
        review_codes = self._merge_review_codes(
            evaluation.review_codes, ownership_review_codes
        )
        status_value = self._status_for(evaluation.matched, review_codes)

        checkin = self._repo.create_structured(
            patient_id=patient.id,
            checkin_date=checkin_date,
            timezone=tz_name,
            responses=self._build_responses(data),
            status=status_value,
            needs_review=bool(review_codes),
            review_reason=",".join(review_codes) if review_codes else None,
        )
        self._db.flush()

        escalations: list[Escalation] = []
        if evaluation.matched and self._settings.checkin_escalation_enabled:
            # `commit=False` throughout: the check-in, its escalations, and
            # their caregiver notices are ONE unit of work, committed once at
            # the end.  Letting either sub-service commit would make a failure
            # in the second escalation leave the first permanently recorded
            # with no way to roll it back.
            escalations = self._escalations.record_matches(
                checkin=checkin, evaluation=evaluation, commit=False
            )
            # Best effort, inline.  A caregiver alert is more useful in the
            # request that raised it than up to a beat-interval later, and the
            # Celery task re-attempts anything this could not materialise, so a
            # failure here is not a lost notification - it is a row that the
            # next run picks up.  `request_caregiver_notification` already
            # records why it declined, and never raises for a missing contact.
            for escalation in escalations:
                self._escalations.request_caregiver_notification(
                    escalation.id, commit=False
                )

        self._commit_or_conflict(checkin)
        self._db.refresh(checkin)
        return self._to_response(
            checkin,
            escalations or self._escalations.list_for_checkin(checkin.id),
        )

    def _commit_or_conflict(self, checkin: CheckIn) -> None:
        """
        Commit, translating a lost race into the documented conflict.

        The preflight lookup above is a convenience, not a guarantee: two
        requests for the same patient and day can both pass it, and only the
        unique index can decide.  So the index can legitimately fire here, on
        a request that looked like a fresh submission a moment ago.

        It is reported as `CheckInAlreadySubmittedError` rather than surfacing
        as a 500: the client's action is the same in both cases - it already has
        a check-in for that day - and a 500 would tell the caller to retry
        something that will fail identically forever.

        The rollback is required before raising; the session is left reusable by
        the route's own cleanup rather than carrying a poisoned transaction.
        """
        try:
            self._db.commit()
        except IntegrityError as exc:
            self._db.rollback()
            logger.info(
                "checkin_duplicate_race patient_id=%s date=%s",
                checkin.patient_id,
                checkin.date,
            )
            raise CheckInAlreadySubmittedError() from exc

    # ── Reads ───────────────────────────────────────────────────────────────
    def get_for_patient(
        self, patient_id: uuid.UUID, checkin_id: uuid.UUID
    ) -> DailyCheckInResponse:
        """
        One check-in, addressed through the patient who owns it.

        The 404 also covers "belongs to someone else".  Returning 403 would
        confirm the id exists, which turns this endpoint into an oracle for
        discovering other patients' check-in ids.
        """
        checkin = self._repo.get_by_id(checkin_id)
        if checkin is None or checkin.patient_id != patient_id:
            raise CheckInNotFoundError()
        return self._to_response(
            checkin, self._escalations.list_for_checkin(checkin.id)
        )

    def get_latest(self, patient_id: uuid.UUID) -> Optional[DailyCheckInResponse]:
        """
        The most recent check-in, or None.

        Returns None rather than 404 for a patient who has not checked in yet:
        "no check-ins yet" is the normal state on day one, not a missing
        resource.
        """
        checkin = self._repo.get_latest_for_patient(patient_id)
        if checkin is None:
            return None
        return self._to_response(
            checkin, self._escalations.list_for_checkin(checkin.id)
        )

    def history(
        self,
        patient_id: uuid.UUID,
        *,
        skip: int = 0,
        limit: int = 100,
        start_date: Optional[dt.date] = None,
        end_date: Optional[dt.date] = None,
    ) -> tuple[int, list[DailyCheckInHistoryItem]]:
        """`(total, page)` for a paged history envelope."""
        checkins = self._repo.list_history_for_patient(
            patient_id,
            skip=skip,
            limit=limit,
            start_date=start_date,
            end_date=end_date,
        )
        items = [
            DailyCheckInHistoryItem(
                id=checkin.id,
                date=checkin.date,
                timezone=checkin.timezone,
                status=checkin.status,
                needs_review=checkin.needs_review,
                review_reason=checkin.review_reason,
                responses=checkin.responses,
                escalation_count=len(
                    self._escalations.list_for_checkin(checkin.id)
                ),
                completed_at=checkin.completed_at,
            )
            for checkin in checkins
        ]
        return self._repo.count_for_patient(patient_id), items

    def questions_for_patient(self, patient: Patient):
        """The question set, with this patient's own warning symptoms."""
        owned = self._load_owned_symptoms(patient.id)
        return build_question_set(owned)

    # ── Internals ───────────────────────────────────────────────────────────
    def _resolve_date_and_timezone(
        self, patient: Patient, data: DailyCheckInCreate
    ) -> tuple[dt.date, str]:
        """
        Work out which calendar day this check-in belongs to.

        Default is today IN THE PATIENT'S ZONE, not the server's date.  This is
        the difference between a correct record and a wrong one for any patient
        whose local day differs from the host's: a patient in Asia/Kolkata
        answering at 00:30 local time has filed today's check-in under
        yesterday if the server's date is used, and their history silently
        shifts by a day.

        A submitted timezone must match the patient's recorded zone.  Accepting
        an arbitrary zone would let one check-in be filed against a date that
        disagrees with every other check-in for the same patient, and there is
        no clinical reason for a patient to be answering from somewhere else.
        """
        patient_tz = (patient.timezone or DEFAULT_TIMEZONE).strip()
        if data.timezone and data.timezone != patient_tz:
            raise CheckInValidationError(
                "The submitted timezone does not match the timezone on the "
                "patient record. A check-in is recorded in the patient's own "
                "timezone."
            )
        if data.date is None:
            resolved = today_in_zone(patient_tz)
        else:
            # A caller may file a backdated day, but not a future one: a
            # check-in dated tomorrow is not a check-in, and accepting it would
            # let history be written with answers that do not exist yet.
            resolved = data.date
            if resolved > today_in_zone(patient_tz):
                raise CheckInValidationError(
                    "A check-in cannot be recorded for a future date."
                )
        return resolved, patient_tz

    def _validate_report_count(self, data: DailyCheckInCreate) -> None:
        limit = self._settings.checkin_max_symptom_reports
        if len(data.warning_symptoms) > limit:
            raise CheckInValidationError(
                f"Too many warning symptom entries: "
                f"{len(data.warning_symptoms)} submitted, limit is {limit}."
            )

    def _load_owned_symptoms(self, patient_id: uuid.UUID) -> list[WarningSymptom]:
        """
        This patient's warning symptoms, and only theirs.

        The patient filter is in the query, so the set is correct by
        construction.  Every downstream membership test is then a genuine
        ownership test rather than a "does this id exist somewhere" test - and
        a request naming another patient's symptom id simply misses.
        """
        return list(self._symptoms.list_for_patient(patient_id))

    def _build_reports(
        self,
        data: DailyCheckInCreate,
        owned: Sequence[WarningSymptom],
    ) -> tuple[list[SymptomReport], list[str]]:
        """
        Match reported symptom ids against the patient's own stored symptoms.

        `requested` is a mapping, so a duplicate id cannot carry two
        contradictory changes into the evaluator - the schema rejects that
        first, and the mapping would collapse it silently if it did not.
        """
        requested = {
            report.symptom_id: report.change for report in data.warning_symptoms
        }
        owned_by_id = {symptom.id: symptom for symptom in owned}
        return build_reports(requested=requested, owned_symptoms=owned_by_id)

    def _rule_set(self) -> RuleSet:
        """
        The effective rule set for this deployment.

        Built per submission rather than cached, so a settings change takes
        effect without a restart-dependent stale object.  It is a cheap pure
        function, and correctness is worth more here than the microseconds.
        """
        return RuleSet(
            enabled_codes=self._settings.checkin_active_rule_codes,
            severity_floor=self._settings.checkin_severity_floor_value,
        )

    @staticmethod
    def _merge_review_codes(
        rule_codes: Sequence[str], ownership_codes: Sequence[str]
    ) -> list[str]:
        """
        Combine the rule layer's review codes with the ownership check's.

        Deduplicated, order-preserving.  The ownership codes come first because
        they describe the input rather than the evaluation: a check-in that
        referenced a symptom the patient does not have is unevaluable for a
        reason a reviewer needs to see before looking at any rule output.
        """
        merged: list[str] = []
        for code in list(ownership_codes) + list(rule_codes):
            if code not in merged:
                merged.append(code)
        return merged

    @staticmethod
    def _status_for(matched: Sequence[object], review_codes: Sequence[str]) -> CheckInStatus:
        """
        Collapse the evaluation into one status.

        `escalated` wins over `needs_review` because an escalation is the more
        urgent fact and a caller filtering on status must not miss a check-in
        that both matched a rule and had an unreadable answer.  The
        `needs_review` flag is still set on the row, so the second fact is not
        lost - it is just not what the single status column leads with.
        """
        if matched:
            return CheckInStatus.escalated
        if review_codes:
            return CheckInStatus.needs_review
        return CheckInStatus.completed

    @staticmethod
    def _build_responses(data: DailyCheckInCreate) -> dict:
        """
        Persist the answers as CODED values.

        No prose is stored, because no prose can arrive - `DailyCheckInCreate`
        has no text field.  The shape is explicit rather than `model_dump()` so
        that adding a field to the schema does not silently start persisting it:
        a new field would be a clinical answer, and it should have to be
        considered here first.

        Enum members are dumped to their `.value`, not `str(member)`, so the
        stored JSON is stable across Python versions and readable by anything
        that is not this codebase.
        """
        return {
            "general_wellbeing": (
                data.general_wellbeing.value
                if data.general_wellbeing is not None
                else None
            ),
            "condition_change": (
                data.condition_change.value
                if data.condition_change is not None
                else None
            ),
            "warning_symptoms": [
                {
                    "symptom_id": str(report.symptom_id),
                    "change": report.change.value,
                }
                for report in data.warning_symptoms
            ],
        }

    def _to_response(
        self, checkin: CheckIn, escalations: Sequence[Escalation]
    ) -> DailyCheckInResponse:
        return DailyCheckInResponse(
            id=checkin.id,
            patient_id=checkin.patient_id,
            date=checkin.date,
            timezone=checkin.timezone or DEFAULT_TIMEZONE,
            status=checkin.status,
            responses=checkin.responses,
            needs_review=checkin.needs_review,
            review_reason=checkin.review_reason,
            completed_at=checkin.completed_at,
            created_at=checkin.created_at,
            escalations=[
                EscalationSummary(
                    id=escalation.id,
                    checkin_id=escalation.checkin_id,
                    rule_code=escalation.rule_code,
                    rule_version=escalation.rule_version,
                    category=escalation.category,
                    workflow=escalation.workflow,
                    status=escalation.status,
                    severity=escalation.severity,
                    reason_code=escalation.reason_code,
                    created_at=escalation.created_at,
                )
                for escalation in escalations
            ],
            patient_message=self._patient_message(
                checkin.status, escalations
            ),
        )

    @staticmethod
    def _patient_message(
        status: CheckInStatus, escalations: Sequence[Escalation]
    ) -> str:
        """
        Assemble the one sentence-set a patient sees.

        Chosen from the CONFIGURED workflow, which an operator selected in
        advance, so this is a lookup rather than a generation.  That is the
        point: a message assembled per patient is a message a language model
        could have written, and this system does not let one.

        With several escalations, the most urgent CONFIGURED workflow wins.  The
        ordering is over workflows - the values a deployment configured - and
        not over any severity the system computed.
        """
        if not escalations:
            if status == CheckInStatus.needs_review:
                return MESSAGE_NEEDS_REVIEW
            return MESSAGE_RECORDED
        for workflow in WORKFLOW_PRIORITY:
            if any(
                escalation.workflow == workflow for escalation in escalations
            ):
                return MESSAGES[workflow]
        return MESSAGE_RECORDED
