"""
CareLoop AI — Timezone Utilities (Phase 5)

All scheduling decisions in Phase 5 funnel through this module.  It exists as
a single choke point for two reasons:

  1. Correctness under DST.  "08:00 daily in America/New_York" is not a fixed
     UTC offset.  It is 13:00Z in winter and 12:00Z in summer.  Converting the
     wall-clock time to an instant must therefore go through `ZoneInfo`, never
     through a fixed offset, and the result must be validated - a local time can
     be *nonexistent* (spring forward) or *ambiguous* (fall back).

  2. One place to audit.  If every reminder's instant is computed here, then
     "is the timezone arithmetic right?" is a question about one file.

Storage convention: every instant is stored and compared as UTC.  The patient's
IANA zone name is stored *alongside* it, so history can be rendered back in
local time without guessing.  The database never holds a naive datetime.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from app.core.exceptions import InvalidTimezoneError

logger = logging.getLogger(__name__)

# The zone assumed when nothing better is known.  UTC is the same assumption
# the pre-Phase-5 system already made, so adopting it changes no existing
# behaviour; it is corrected explicitly, never guessed.
DEFAULT_TIMEZONE = "UTC"

# Cache: ZoneInfo construction hits the filesystem and available_timezones()
# scans the whole tzdata tree.  Both are effectively constant per process.
_ZONE_CACHE: dict[str, ZoneInfo] = {}
_KNOWN_ZONES: frozenset[str] | None = None


def _known_zones() -> frozenset[str]:
    """All IANA zone names this platform recognises, memoised."""
    global _KNOWN_ZONES
    if _KNOWN_ZONES is None:
        try:
            _KNOWN_ZONES = frozenset(available_timezones())
        except Exception:  # pragma: no cover - only if tzdata is absent
            logger.warning(
                "tzdata unavailable; falling back to ZoneInfo-based validation"
            )
            _KNOWN_ZONES = frozenset()
    return _KNOWN_ZONES


def resolve_timezone(name: str | None) -> ZoneInfo:
    """
    Return a `ZoneInfo` for ``name``, or raise `InvalidTimezoneError`.

    Rejecting an unknown zone is the point.  Silently falling back to UTC would
    shift a patient's medication by hours without anybody noticing, which for a
    dose schedule is a safety defect, not a convenience.
    """
    if name is None or not str(name).strip():
        raise InvalidTimezoneError(
            "A timezone is required. Supply an IANA name such as "
            "'America/New_York'."
        )

    candidate = str(name).strip()
    cached = _ZONE_CACHE.get(candidate)
    if cached is not None:
        return cached

    try:
        zone = ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        # ZoneInfo raises ZoneInfoNotFoundError for a well-formed but unknown
        # name and ValueError for something that is not a zone name at all
        # (e.g. "Not/AZone", or a raw "UTC+5:30" offset).  Both are the
        # caller's 422, not a server fault.
        raise InvalidTimezoneError(
            f"Unknown timezone: {candidate!r}. Use an IANA name such as "
            "'America/New_York' or 'Asia/Kolkata'."
        ) from exc

    # A ZoneInfo can construct successfully yet be meaningless (an arbitrary
    # directory name on some platforms), so cross-check the name list when
    # tzdata is present.
    known = _known_zones()
    if known and candidate not in known and candidate != "UTC":
        raise InvalidTimezoneError(
            f"Unknown timezone: {candidate!r}. Use an IANA name such as "
            "'America/New_York' or 'Asia/Kolkata'."
        )

    _ZONE_CACHE[candidate] = zone
    return zone


def validate_timezone_name(name: str | None) -> str:
    """
    Validate a timezone and return its canonical form.

    Use this at the schema/validation boundary so an invalid zone is rejected
    before a service ever runs a transaction.
    """
    return str(resolve_timezone(name).key)


def is_valid_timezone(name: str | None) -> bool:
    """Boolean form of `validate_timezone_name`, for non-raising callers."""
    try:
        validate_timezone_name(name)
        return True
    except InvalidTimezoneError:
        return False


def ensure_aware(value: datetime, *, field: str = "datetime") -> datetime:
    """
    Return ``value`` as an aware UTC datetime, or raise.

    A naive datetime reaching this module is a bug, not something to guess at.
    Phase 2's extractor attaches UTC to the values it parses, so those arrive
    aware; anything else is treated as a caller error rather than assumed to be
    UTC, because assuming would silently shift a clinical timestamp.
    """
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime, got {type(value).__name__}")

    if value.tzinfo is None:
        raise ValueError(
            f"{field} must be timezone-aware; a naive datetime cannot be "
            "scheduled reliably."
        )

    return value.astimezone(timezone.utc)


def utcnow() -> datetime:
    """Timezone-aware current time in UTC. Single source of 'now'."""
    return datetime.now(timezone.utc)


def to_local(value: datetime, tz_name: str) -> datetime:
    """Render an aware UTC instant in the patient's zone."""
    return ensure_aware(value).astimezone(resolve_timezone(tz_name))


def local_naive_to_utc(local_dt: datetime, tz_name: str) -> datetime:
    """
    Convert a *wall-clock* local datetime to a UTC instant.

    This is the function that must handle DST, because the input has no offset
    of its own - "2026-03-08 02:30 in America/New_York" is genuinely ambiguous
    as a bare datetime.

    Two pathological cases are handled explicitly rather than left to whatever
    the platform happens to do:

      * Non-existent (spring forward, 02:00-03:00 skipped).  Attaching the
        zone yields a time that never occurs.  The instant is moved forward to
        the first time that DOES exist (03:00 local), so a reminder still fires
        that day - the standard "push forward" resolution.
      * Ambiguous (fall back, 01:00-02:00 repeated).  Attaching the zone picks
        one arbitrarily.  `fold=0`, the first/earlier occurrence, is chosen
        deterministically so the same reminder never lands on two different
        instants.
    """
    zone = resolve_timezone(tz_name)
    if local_dt.tzinfo is not None:
        # Already carries an offset: trust it and normalise.
        return local_dt.astimezone(timezone.utc)

    aware = local_dt.replace(tzinfo=zone, fold=0)
    normalised = aware.astimezone(timezone.utc).astimezone(zone)

    if normalised.replace(tzinfo=None) != local_dt:
        # The wall-clock time does not exist in this zone.  Shift forward to
        # the first valid instant after it.
        shifted = (local_dt + timedelta(hours=1)).replace(tzinfo=zone, fold=0)
        candidate = shifted.astimezone(timezone.utc).astimezone(zone)
        # Guard the pathological case where +1h is also invalid (a whole hour
        # skipped plus more): walk forward until a real time is found, bounded
        # so this can never spin.
        for _ in range(48):
            if candidate.replace(tzinfo=None) >= local_dt:
                break
            candidate = (candidate + timedelta(minutes=15)).astimezone(zone)
        logger.info(
            "Reminder local time does not exist in %s (DST gap); shifted forward",
            tz_name,
        )
        return candidate.astimezone(timezone.utc)

    return aware.astimezone(timezone.utc)


def next_daily_occurrence(
    after: datetime,
    tz_name: str,
    local_time: time,
) -> datetime:
    """
    First UTC instant strictly after ``after`` at ``local_time`` local wall time.

    Used to advance a recurring medication reminder.  The comparison is done in
    LOCAL time by constructing today's candidate, which is what makes the
    result DST-correct: the scheduler always re-derives the next instant from
    the zone rather than adding 24h to a UTC timestamp (which would drift by an
    hour twice a year).
    """
    zone = resolve_timezone(tz_name)
    after_utc = ensure_aware(after, field="after")
    local_now = after_utc.astimezone(zone)

    candidate_local = datetime.combine(local_now.date(), local_time)
    candidate_utc = local_naive_to_utc(candidate_local, tz_name)

    if candidate_utc > after_utc:
        return candidate_utc

    # Today's dose time has passed: try tomorrow.
    tomorrow = local_now.date() + timedelta(days=1)
    return local_naive_to_utc(datetime.combine(tomorrow, local_time), tz_name)


def next_weekly_occurrence(
    after: datetime,
    tz_name: str,
    local_time: time,
    weekday: int,
    interval: int = 1,
) -> datetime:
    """
    First UTC instant strictly after ``after`` for a weekly cadence.

    ``weekday`` follows `datetime.weekday()` (0 = Monday).  ``interval`` is in
    weeks.  Computed in local date space so a DST boundary inside the week
    cannot shift the day-of-week.
    """
    if not 0 <= weekday <= 6:
        raise ValueError(f"weekday must be 0-6, got {weekday}")
    if interval < 1:
        raise ValueError(f"interval must be >= 1, got {interval}")

    zone = resolve_timezone(tz_name)
    after_utc = ensure_aware(after, field="after")
    local_now = after_utc.astimezone(zone)

    candidate_date = local_now.date()
    days_ahead = (weekday - candidate_date.weekday()) % 7
    candidate_date = candidate_date + timedelta(days=days_ahead)

    candidate_utc = local_naive_to_utc(
        datetime.combine(candidate_date, local_time), tz_name
    )
    if candidate_utc > after_utc:
        return candidate_utc

    # This week's slot has passed: add the recurrence interval.
    weeks = interval
    for _ in range(52):
        candidate_date = candidate_date + timedelta(weeks=weeks)
        candidate_utc = local_naive_to_utc(
            datetime.combine(candidate_date, local_time), tz_name
        )
        if candidate_utc > after_utc:
            return candidate_utc
        weeks = interval
    raise ValueError("Could not resolve a future weekly occurrence")


def describe_occurrence(instant_utc: datetime, tz_name: str) -> str:
    """
    Human-readable local rendering, for logs and history views.

    Includes the UTC offset so a support engineer can tell DST from a bug.
    """
    local = to_local(instant_utc, tz_name)
    return f"{local.strftime('%Y-%m-%d %H:%M')} ({local.strftime('%z')})"


def today_in_zone(tz_name: str, *, at: datetime | None = None) -> date:
    """The calendar date in the patient's zone at ``at`` (default: now)."""
    return to_local(at or utcnow(), tz_name).date()
