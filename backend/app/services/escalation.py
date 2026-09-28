"""
CareLoop AI — Escalation Service (Phase 6)

Owns the escalation lifecycle and the caregiver-notification decision.

THE TWO THINGS THIS SERVICE REFUSES TO DO
1. Invent clinical meaning.  It receives matches that
   `app.services.red_flag_rules` already produced from stored facts, and it
   writes them down.  It never re-decides whether a rule should have fired, and
   it never enriches a row with an assessment the rule layer did not make.

2. Send anything to anyone it is not sure about.  A caregiver notice is
   materialised only when the patient record carries an explicit
   `caregiver_contact`.  When it does not, the escalation is left in `pending`
   with `notification_blocked_reason` set - visible, auditable, and waiting for
   a human to fix the patient record.  It is never quietly redirected to the
   patient's own number, and it is never silently dropped.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    CheckInNotFoundError,
    EscalationNotFoundError,
    EscalationTransitionError,
)
from app.core.timezones import utcnow
from app.models.checkin import CheckIn
from app.models.escalation import Escalation, EscalationStatus
from app.models.notification import Notification
from app.models.patient import Patient
from app.repositories.checkin import CheckInRepository
from app.repositories.escalation import EscalationRepository
from app.services.notification import NotificationService

logger = logging.getLogger(__name__)

#: Set on an escalation whose caregiver notice could not be built.  A code, not
#: prose: it is shown in the API, and it must not be able to carry a phone
#: number or any other record detail into a log line.
BLOCKED_NO_CAREGIVER_CONTACT = "caregiver_contact_missing"
BLOCKED_NOTIFICATIONS_DISABLED = "caregiver_notifications_disabled"
BLOCKED_ESCALATIONS_DISABLED = "escalation_disabled"

#: The transitions this service will perform.  Declared as data rather than as
#: scattered `if` statements so the whole lifecycle is readable in one place and
#: a test can assert the table is exactly what is documented.
ALLOWED_TRANSITIONS: dict[EscalationStatus, frozenset[EscalationStatus]] = {
    EscalationStatus.pending: frozenset(
        {
            EscalationStatus.notified,
            EscalationStatus.cancelled,
        }
    ),
    EscalationStatus.notified: frozenset(
        {
            EscalationStatus.acknowledged,
            EscalationStatus.cancelled,
        }
    ),
    EscalationStatus.acknowledged: frozenset({EscalationStatus.resolved}),
    # Terminal states.  A resolved escalation is not re-opened: reopening would
    # mean a human decided the loop was closed and then un-decided it, and the
    # audit trail would show a resolution that never really happened.
    EscalationStatus.resolved: frozenset(),
    EscalationStatus.cancelled: frozenset(),
}


class EscalationService:
    """
    Records escalations, notifies caregivers, and moves escalations through
    their lifecycle.

    Depends on `NotificationService` for delivery so the Phase 5 retry policy
    and delivery history apply to caregiver alerts unchanged - an escalation
    notice is not a special kind of message that deserves its own transport.
    """

    def __init__(
        self,
        db: Session,
        *,
        settings: Optional[Settings] = None,
        notification_service: Optional[NotificationService] = None,
    ) -> None:
        self._db = db
        self._repo = EscalationRepository(db)
        self._checkins = CheckInRepository(db)
        self._settings = settings or get_settings()
        self._notifications = notification_service or NotificationService(db)

    # ── Recording ───────────────────────────────────────────────────────────
    def record_matches(
        self,
        *,
        checkin: CheckIn,
        evaluation,
        commit: bool = True,
    ) -> list[Escalation]:
        """
        Persist one escalation per matched rule, exactly once.

        `evaluation` is the `Evaluation` returned by the rule layer.  The
        (checkin, rule) pair is checked before inserting, and the unique index
        `uq_escalations_checkin_rule` is the real guard, so calling this twice -
        from a retried request, a duplicate Celery task, or an operator
        pressing the button again - cannot produce a duplicate escalation or a
        duplicate alert.

        Returns the escalations for this check-in, whether newly written or
        already present, so the caller always sees the complete set.

        `commit=False` lets a caller that owns a larger unit of work keep the
        transaction open.  `DailyCheckInService.submit` needs that: a check-in
        and its escalations are one fact, and committing halfway through the
        matched rules would leave a check-in marked `escalated` alongside
        escalations that a later failure never persisted.
        """
        created = False
        for match in evaluation.matched:
            existing = self._repo.get_by_checkin_and_rule(
                checkin.id, match.rule.code
            )
            if existing is not None:
                continue

            report = match.report
            escalation = Escalation(
                patient_id=checkin.patient_id,
                checkin_id=checkin.id,
                rule_code=match.rule.code,
                rule_version=evaluation.rule_version,
                category=match.rule.category,
                workflow=match.rule.workflow,
                status=EscalationStatus.pending,
                # Copied, never derived.  See red_flag_rules for why the rule
                # layer is not allowed to compute a severity.
                severity=report.severity.value if report is not None else None,
                warning_symptom_id=report.symptom_id if report is not None else None,
                # A safe, non-diagnostic string: rule code, rule version, and
                # the stored severity label.  No answers, no symptom text.
                reason_code=evaluation.reason_code(match),
            )
            self._repo.create(escalation)
            created = True
            logger.info(
                "escalation_recorded escalation_id=%s checkin_id=%s rule=%s",
                escalation.id,
                checkin.id,
                match.rule.code,
            )

        if created and commit:
            self._db.commit()
        return self._repo.list_for_checkin(checkin.id)

    # ── Caregiver notification ──────────────────────────────────────────────
    def request_caregiver_notification(
        self, escalation_id: uuid.UUID, *, commit: bool = True
    ) -> Optional[Notification]:
        """
        Build the caregiver notification for one escalation, if it can be.

        Returns the `Notification`, or `None` when no notification was built -
        which happens for three distinct and separately-reported reasons:

          * the escalation is not `pending` (it was already notified, or a human
            has since acknowledged or resolved it, and un-notifying would
            contradict them);
          * `CHECKIN_NOTIFY_CAREGIVER` is off for this deployment, so nothing is
            sent while the escalation stays fully visible in the API;
          * the patient has no `caregiver_contact` on file.

        In every "no" case the reason is written to
        `notification_blocked_reason`, so an escalation that is not being
        acted on is visible as such instead of looking healthy.

        Delivery is NOT attempted here.  Materialising the row and handing it to
        the provider are separate steps, exactly as in Phase 5: a request should
        not block on a network call, and a crash between the two leaves a
        pending notification that the Phase 5 retry path already knows how to
        pick up.
        """
        escalation = self._repo.get_by_id(escalation_id)
        if escalation is None:
            raise EscalationNotFoundError()

        if escalation.status != EscalationStatus.pending:
            logger.info(
                "escalation_notification_skipped escalation_id=%s reason=%s",
                escalation_id,
                escalation.status.value,
            )
            return None

        if not self._settings.checkin_escalation_enabled:
            return self._block(
                escalation, BLOCKED_ESCALATIONS_DISABLED, commit=commit
            )

        if not self._settings.checkin_notify_caregiver:
            return self._block(
                escalation, BLOCKED_NOTIFICATIONS_DISABLED, commit=commit
            )

        patient = self._db.get(Patient, escalation.patient_id)
        caregiver_contact = (
            (patient.caregiver_contact or "").strip() if patient else ""
        )
        if not caregiver_contact:
            # The case this whole branch exists for.  No fallback to the
            # patient's own number: see NotificationService.materialize_escalation.
            return self._block(
                escalation, BLOCKED_NO_CAREGIVER_CONTACT, commit=commit
            )

        notification, created = self._notifications.materialize_escalation(
            escalation,
            caregiver_contact=caregiver_contact,
            patient_timezone=patient.timezone if patient else None,
        )
        if not created:
            # A previous run already materialised it.  Re-attaching is a no-op
            # and must not clear a blocked reason that a human set deliberately.
            logger.info(
                "escalation_notification_existing escalation_id=%s "
                "notification_id=%s",
                escalation_id,
                notification.id,
            )
            return notification

        self._repo.update(
            escalation,
            notification_id=notification.id,
            notification_blocked_reason=None,
        )
        if commit:
            self._db.commit()
        logger.info(
            "escalation_notification_materialized escalation_id=%s "
            "notification_id=%s",
            escalation.id,
            notification.id,
        )
        return notification

    def _block(
        self,
        escalation: Escalation,
        reason_code: str,
        *,
        commit: bool = True,
    ) -> None:
        """
        Park an escalation that cannot be notified, and say why.

        Deliberately does not move the escalation out of `pending`: it stays
        pending, it is simply not being sent, and an operator can see the reason
        on the row.  Returns None so the caller can `return self._block(...)`.
        """
        self._repo.update(
            escalation, notification_blocked_reason=reason_code
        )
        if commit:
            self._db.commit()
        logger.warning(
            "escalation_notification_blocked escalation_id=%s reason=%s",
            escalation.id,
            reason_code,
        )
        return None

    def notify_pending(self, *, limit: int = 100) -> dict[str, int]:
        """
        Attempt a caregiver notification for every eligible pending escalation.

        This is what the Celery task calls, and it is safe to run repeatedly:
        an escalation that already has a notification is excluded by the
        repository query, and one that is blocked is excluded too, so a blocked
        escalation does not fill the log with a condition retrying cannot fix.

        A failure on one escalation does not stop the batch - the remaining
        escalations are still worth notifying, and the failed one is retried on
        the next run because it is still pending with no notification.
        """
        now = utcnow()
        candidates = self._repo.find_pending_untried(
            now=now, limit=limit
        )
        materialized = 0
        blocked = 0
        failed = 0
        for escalation in candidates:
            try:
                notification = self.request_caregiver_notification(
                    escalation.id
                )
            except Exception:  # noqa: BLE001 - one bad row must not stop the batch
                self._db.rollback()
                failed += 1
                logger.exception(
                    "escalation_notification_error escalation_id=%s",
                    escalation.id,
                )
                continue
            if notification is None:
                blocked += 1
            else:
                materialized += 1
        return {
            "examined": len(candidates),
            "materialized": materialized,
            "blocked": blocked,
            "failed": failed,
        }

    # ── Lifecycle ───────────────────────────────────────────────────────────
    def get(self, escalation_id: uuid.UUID) -> Escalation:
        escalation = self._repo.get_by_id(escalation_id)
        if escalation is None:
            raise EscalationNotFoundError()
        return escalation

    def list_for_checkin(self, checkin_id: uuid.UUID) -> list[Escalation]:
        return self._repo.list_for_checkin(checkin_id)

    def list_for_patient(
        self,
        patient_id: uuid.UUID,
        *,
        skip: int = 0,
        limit: int = 100,
        status: Optional[EscalationStatus] = None,
    ) -> list[Escalation]:
        return self._repo.list_for_patient(
            patient_id, skip=skip, limit=limit, status=status
        )

    def count_for_patient(
        self, patient_id: uuid.UUID, status: Optional[EscalationStatus] = None
    ) -> int:
        return self._repo.count_for_patient(patient_id, status=status)

    def acknowledge(self, escalation_id: uuid.UUID) -> Escalation:
        """
        Record that a human has seen the escalation.

        Only from `notified`.  Acknowledging a `pending` escalation is refused:
        it would assert that a caregiver saw an alert that was never sent, which
        is precisely the false record the status column exists to prevent.
        """
        escalation = self._get_for_transition(escalation_id)
        self._require_transition(
            escalation, EscalationStatus.acknowledged
        )
        return self._repo.update(
            escalation,
            status=EscalationStatus.acknowledged,
            acknowledged_at=utcnow(),
        )

    def resolve(
        self,
        escalation_id: uuid.UUID,
        *,
        note: Optional[str] = None,
    ) -> Escalation:
        """Close the loop, optionally with a short human-written note."""
        escalation = self._get_for_transition(escalation_id)
        self._require_transition(escalation, EscalationStatus.resolved)
        return self._repo.update(
            escalation,
            status=EscalationStatus.resolved,
            resolved_at=utcnow(),
            resolution_note=note,
        )

    def cancel(self, escalation_id: uuid.UUID) -> Escalation:
        """
        Withdraw an escalation that should not have been raised.

        Distinct from `resolve`: resolving says the loop was followed and is
        finished, cancelling says the flag itself was not actionable.  A human
        still has to say which, so the audit trail cannot quietly turn "this was
        a false alarm" into "this was handled".
        """
        escalation = self._get_for_transition(escalation_id)
        self._require_transition(escalation, EscalationStatus.cancelled)
        return self._repo.update(
            escalation, status=EscalationStatus.cancelled
        )

    def _get_for_transition(self, escalation_id: uuid.UUID) -> Escalation:
        escalation = self._repo.get_by_id(escalation_id)
        if escalation is None:
            raise EscalationNotFoundError()
        return escalation

    def _require_transition(
        self, escalation: Escalation, target: EscalationStatus
    ) -> None:
        allowed = ALLOWED_TRANSITIONS.get(escalation.status, frozenset())
        if target not in allowed:
            raise EscalationTransitionError(
                f"An escalation in status {escalation.status.value!r} cannot "
                f"move to {target.value!r}. Allowed from here: "
                f"{sorted(s.value for s in allowed) or ['none - terminal']}."
            )
        # `update` flushes; the caller commits with its route.
        self._db.flush()
