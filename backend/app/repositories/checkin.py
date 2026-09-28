"""
CareLoop AI — CheckIn Repository

Phase 1 queries are unchanged and still work.  Phase 6 adds the queries the
structured check-in needs - one per patient and date, latest, and a paged
history that counts matching rows without loading them.

`get_by_patient_and_date` is deliberately patient-scoped rather than a plain
`get_by_id`: a check-in is only ever addressable through the patient it belongs
to, so an unfiltered fetch is not something this repository offers.
"""
from __future__ import annotations

import uuid
import datetime as dt
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.timezones import utcnow
from app.models.checkin import CheckIn, CheckInStatus
from app.schemas.checkin import CheckInCreate


def _as_date(value: object, field: str) -> Optional[dt.date]:
    """
    Coerce a date filter to `date`, accepting an ISO string.

    Returns `None` unchanged so callers can pass an absent bound straight
    through. A string that is not a date raises `ValueError` with the field
    name, which is a far better failure than the database error it replaces.
    """
    if value is None or isinstance(value, dt.date):
        return value  # type: ignore[return-value]
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(
                f"{field} must be an ISO date (YYYY-MM-DD), got {value!r}."
            ) from exc
    raise TypeError(f"{field} must be a date or ISO string, got {type(value)}.")


class CheckInRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create(self, patient_id: uuid.UUID, data: CheckInCreate) -> CheckIn:
        checkin = CheckIn(patient_id=patient_id, **data.model_dump())
        self._db.add(checkin)
        self._db.flush()
        self._db.refresh(checkin)
        return checkin

    def get_by_id(self, checkin_id: uuid.UUID) -> Optional[CheckIn]:
        stmt = select(CheckIn).where(CheckIn.id == checkin_id)
        return self._db.scalars(stmt).first()

    def list_for_patient(
        self, patient_id: uuid.UUID, skip: int = 0, limit: int = 100
    ) -> List[CheckIn]:
        """
        Phase 1 listing: the legacy free-text check-ins only.

        A structured Phase 6 row has `response_text IS NULL`, so returning one
        here would fail response validation on the Phase 1 schema (which
        requires prose) and turn an ordinary list request into a 500 for any
        patient who has used both endpoints.  Excluding them keeps each surface
        describing exactly the record kind it promises.
        """
        stmt = (
            select(CheckIn)
            .where(
                CheckIn.patient_id == patient_id,
                CheckIn.responses.is_(None),
            )
            .order_by(CheckIn.date.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(self._db.scalars(stmt).all())

    def get_legacy_by_id(self, checkin_id: uuid.UUID) -> Optional[CheckIn]:
        """
        One legacy check-in by id, or None if the id is a structured row.

        Same reason as `list_for_patient`: the Phase 1 response schema requires
        `response_text`, so a structured row must 404 rather than 500.
        """
        stmt = select(CheckIn).where(
            CheckIn.id == checkin_id,
            CheckIn.responses.is_(None),
        )
        return self._db.scalars(stmt).first()

    # ── Phase 6 ─────────────────────────────────────────────────────────────

    def create_structured(
        self,
        *,
        patient_id: uuid.UUID,
        checkin_date: dt.date,
        timezone: str,
        responses: dict,
        status: CheckInStatus,
        needs_review: bool,
        review_reason: Optional[str],
        reminder_id: Optional[uuid.UUID] = None,
    ) -> CheckIn:
        """
        Persist an evaluated structured check-in.

        `response_text` is left NULL on purpose: Phase 6 has no free text, and
        writing an empty string would make these rows look like Phase 1 entries
        with a missing answer to any query that does not check the status.

        `completed_at` is stamped here, from the server clock, rather than being
        accepted from the request - it records when the system accepted the
        submission, and a client-settable timestamp would make the audit trail
        editable from outside.
        """
        checkin = CheckIn(
            patient_id=patient_id,
            date=checkin_date,
            response_text=None,
            responses=responses,
            status=status,
            timezone=timezone,
            needs_review=needs_review,
            review_reason=review_reason,
            reminder_id=reminder_id,
            completed_at=utcnow(),
        )
        self._db.add(checkin)
        self._db.flush()
        self._db.refresh(checkin)
        return checkin

    def get_by_patient_and_date(
        self, patient_id: uuid.UUID, checkin_date: dt.date
    ) -> Optional[CheckIn]:
        """
        The patient's check-in for one calendar date, if any.

        Backs the one-check-in-per-day rule.  The unique index
        `uq_checkins_patient_date` is the real guard against a concurrent
        double submission; this turns the common case into a clean 409 instead
        of an IntegrityError surfacing as a 500.
        """
        stmt = select(CheckIn).where(
            CheckIn.patient_id == patient_id,
            CheckIn.date == checkin_date,
        )
        return self._db.scalars(stmt).first()

    def get_latest_for_patient(self, patient_id: uuid.UUID) -> Optional[CheckIn]:
        """
        The most recent STRUCTURED check-in by date, or None.

        Ties on date cannot occur - the unique index forbids two check-ins for
        one patient and date - so ordering by date alone is deterministic.

        Legacy rows are excluded deliberately.  A Phase 1 row was never
        evaluated by the rule layer, so presenting it here would report
        `status=completed` and a reassuring patient message for a submission
        this system never actually checked.  That is a clinical claim about a
        record that carries no answers to check, so the two stay separate.
        """
        stmt = (
            select(CheckIn)
            .where(
                CheckIn.patient_id == patient_id,
                CheckIn.responses.is_not(None),
            )
            .order_by(CheckIn.date.desc(), CheckIn.created_at.desc())
            .limit(1)
        )
        return self._db.scalars(stmt).first()

    def count_for_patient(self, patient_id: uuid.UUID) -> int:
        """Structured check-ins on file, for a paged history envelope."""
        stmt = select(func.count()).select_from(CheckIn).where(
            CheckIn.patient_id == patient_id,
            CheckIn.responses.is_not(None),
        )
        return int(self._db.scalar(stmt) or 0)

    def list_history_for_patient(
        self,
        patient_id: uuid.UUID,
        *,
        skip: int = 0,
        limit: int = 100,
        start_date: Optional[dt.date] = None,
        end_date: Optional[dt.date] = None,
    ) -> List[CheckIn]:
        """
        Paged history, newest first, optionally bounded by date.

        The date range is applied in SQL rather than after loading so a long
        history stays cheap, and so an impossible range returns an empty page
        rather than silently reversing the bounds.

        ISO strings are accepted and parsed. That is not laziness: FastAPI
        coerces a query parameter to `date` on the HTTP path, but a direct
        service caller - a Celery task, a script, a future internal endpoint -
        can easily hand over a string. Passing one straight through produces
        `operator does not exist: date >= character varying` from PostgreSQL,
        which reads like a schema bug rather than a bad argument.
        """
        start_date = _as_date(start_date, "start_date")
        end_date = _as_date(end_date, "end_date")
        stmt = select(CheckIn).where(
            CheckIn.patient_id == patient_id,
            # Structured rows only, for the same reason as
            # `get_latest_for_patient`.
            CheckIn.responses.is_not(None),
        )
        if start_date is not None:
            stmt = stmt.where(CheckIn.date >= start_date)
        if end_date is not None:
            stmt = stmt.where(CheckIn.date <= end_date)
        stmt = (
            stmt.order_by(CheckIn.date.desc(), CheckIn.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(self._db.scalars(stmt).all())
