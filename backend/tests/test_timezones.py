"""
CareLoop AI — Timezone Utility Tests (Phase 5)

These are the highest-value tests in the phase and need no database.  A bug in
the timezone maths does not raise an exception: it quietly moves a patient's
medication to the wrong hour, twice a year, for one patient.  Nothing else in
the suite would catch that.

The cases below are the real-world shapes:
  * the same wall-clock time mapping to two different UTC offsets across DST,
  * a local time that does not exist on the spring-forward day,
  * a local time that happens twice on the fall-back day,
  * a naive datetime being rejected rather than assumed to be UTC.
"""
from datetime import datetime, time, timezone

import pytest

from app.core.exceptions import InvalidTimezoneError
from app.core.timezones import (
    DEFAULT_TIMEZONE,
    ensure_aware,
    is_valid_timezone,
    local_naive_to_utc,
    next_daily_occurrence,
    next_weekly_occurrence,
    resolve_timezone,
    to_local,
    utcnow,
    validate_timezone_name,
)


# ── Validation ───────────────────────────────────────────────────────────────


class TestTimezoneValidation:
    @pytest.mark.parametrize(
        "name",
        [
            "UTC",
            "America/New_York",
            "Asia/Kolkata",
            "Europe/London",
            "Australia/Sydney",
            "America/Sao_Paulo",
        ],
    )
    def test_accepts_real_iana_zones(self, name):
        assert validate_timezone_name(name) == name
        assert is_valid_timezone(name)

    @pytest.mark.parametrize(
        "name",
        [
            "Mars/Phobos",
            "Not/AZone",
            "UTC+5:30",       # a fixed offset is not an IANA zone name
            "",               # empty is not "use the default" - the caller decides
            "   ",
            "not a zone",
            "../../etc/passwd",
        ],
    )
    def test_rejects_unknown_zones(self, name):
        """An unknown zone must fail loudly, not silently fall back to UTC.

        Falling back would shift a dose by hours with no visible error.
        """
        with pytest.raises(InvalidTimezoneError):
            resolve_timezone(name)
        assert is_valid_timezone(name) is False

    @pytest.mark.parametrize("name", ["EST5EDT", "Asia/Calcutta", "US/Eastern"])
    def test_accepts_tzdata_backward_compatibility_links(self, name):
        """
        Legacy IANA links are accepted.

        They are real entries in the tzdata database, and a deployment with an
        older config may legitimately carry one.  Rejecting a name the platform
        resolves would fail a valid request for no safety benefit - the
        resulting offset is still correct.
        """
        if name not in __import__("zoneinfo").available_timezones():
            pytest.skip(f"{name} not present in this platform's tzdata")
        assert is_valid_timezone(name)

    def test_rejects_none(self):
        with pytest.raises(InvalidTimezoneError):
            resolve_timezone(None)

    def test_default_timezone_is_utc(self):
        assert validate_timezone_name(DEFAULT_TIMEZONE) == "UTC"


# ── Aware/naive discipline ───────────────────────────────────────────────────


class TestAwareDatetimeDiscipline:
    def test_rejects_naive_datetime(self):
        """A naive datetime cannot be scheduled reliably, so it is refused."""
        with pytest.raises(ValueError, match="timezone-aware"):
            ensure_aware(datetime(2026, 1, 1, 8, 0))

    def test_aware_utc_passes_through_unchanged(self):
        value = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
        assert ensure_aware(value) == value

    def test_offset_datetime_is_normalised_to_utc(self):
        from datetime import timedelta

        value = datetime(
            2026, 1, 1, 8, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))
        )
        assert ensure_aware(value).utcoffset() == timedelta(0)
        assert ensure_aware(value).hour == 2

    def test_rejects_non_datetime(self):
        with pytest.raises(TypeError):
            ensure_aware("2026-01-01T08:00:00Z")


# ── Local <-> UTC conversion across DST ──────────────────────────────────────


class TestLocalToUtc:
    def test_same_wall_clock_maps_to_different_offsets_across_dst(self):
        """
        The canonical bug this module exists to prevent.

        08:00 in New York is 13:00Z in January (EST) and 12:00Z in July (EDT).
        A fixed-offset implementation gets one of the two wrong, which means
        the patient is reminded at 07:00 or 09:00 local for half the year.
        """
        winter = local_naive_to_utc(
            datetime(2026, 1, 15, 8, 0), "America/New_York"
        )
        summer = local_naive_to_utc(
            datetime(2026, 7, 15, 8, 0), "America/New_York"
        )
        assert winter.strftime("%H:%M") == "13:00"
        assert summer.strftime("%H:%M") == "12:00"

    def test_spring_forward_nonexistent_time_is_pushed_forward(self):
        """
        02:30 on 2026-03-08 does not exist in New York (02:00 -> 03:00).

        The reminder must still fire that day.  Pushing forward by the gap
        keeps the intended minute (:30) rather than snapping to 03:00, which
        would silently change the dosing time.
        """
        instant = local_naive_to_utc(
            datetime(2026, 3, 8, 2, 30), "America/New_York"
        )
        local = to_local(instant, "America/New_York")
        assert local.strftime("%H:%M") == "03:30"
        assert local.date() == datetime(2026, 3, 8).date()

    def test_spring_forward_gap_boundary(self):
        instant = local_naive_to_utc(
            datetime(2026, 3, 8, 2, 0), "America/New_York"
        )
        assert to_local(instant, "America/New_York").strftime("%H:%M") == "03:00"

    def test_valid_time_on_the_gap_day_is_untouched(self):
        """Only the missing hour is affected; 10:00 must stay 10:00."""
        instant = local_naive_to_utc(
            datetime(2026, 3, 8, 10, 0), "America/New_York"
        )
        assert to_local(instant, "America/New_York").strftime("%H:%M") == "10:00"

    def test_fall_back_ambiguous_time_is_deterministic(self):
        """
        01:30 on 2026-11-01 occurs twice in New York.

        `fold` decides which.  It must be the same one every time, or a reminder
        would land on two different instants depending on when it was computed.
        """
        first = local_naive_to_utc(
            datetime(2026, 11, 1, 1, 30), "America/New_York"
        )
        second = local_naive_to_utc(
            datetime(2026, 11, 1, 1, 30), "America/New_York"
        )
        assert first == second
        # fold=0 is the earlier (daylight) occurrence: 01:30 EDT = 05:30Z
        assert first.strftime("%H:%M") == "05:30"

    def test_aware_input_is_normalised_not_reinterpreted(self):
        """An input that already has an offset is trusted, not re-read as local."""
        from datetime import timedelta

        aware = datetime(
            2026, 1, 15, 8, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))
        )
        assert local_naive_to_utc(aware, "America/New_York") == aware.astimezone(
            timezone.utc
        )

    def test_southern_hemisphere_dst(self):
        """Sydney's DST runs the other way; the arithmetic must still hold."""
        january = local_naive_to_utc(
            datetime(2026, 1, 15, 8, 0), "Australia/Sydney"
        )
        july = local_naive_to_utc(
            datetime(2026, 7, 15, 8, 0), "Australia/Sydney"
        )
        assert january.strftime("%H:%M") == "21:00"  # AEDT, UTC+11
        assert july.strftime("%H:%M") == "22:00"     # AEST, UTC+10

    def test_half_hour_offset_zone(self):
        """Kolkata is UTC+5:30, which a whole-hour-only implementation breaks."""
        instant = local_naive_to_utc(datetime(2026, 1, 15, 8, 0), "Asia/Kolkata")
        assert instant.strftime("%H:%M") == "02:30"


# ── Recurrence ───────────────────────────────────────────────────────────────


class TestDailyRecurrence:
    def test_next_occurrence_is_today_when_the_time_has_not_passed(self):
        now = datetime(2026, 1, 15, 6, 0, tzinfo=timezone.utc)
        nxt = next_daily_occurrence(now, "America/New_York", time(8, 0))
        assert to_local(nxt, "America/New_York").strftime("%Y-%m-%d %H:%M") == (
            "2026-01-15 08:00"
        )

    def test_next_occurrence_is_tomorrow_when_the_time_has_passed(self):
        now = datetime(2026, 1, 15, 20, 0, tzinfo=timezone.utc)
        nxt = next_daily_occurrence(now, "America/New_York", time(8, 0))
        assert to_local(nxt, "America/New_York").strftime("%Y-%m-%d %H:%M") == (
            "2026-01-16 08:00"
        )

    def test_daily_chain_holds_local_time_across_the_dst_boundary(self):
        """
        The real test: a 24h-naive implementation fires at 07:00 local after
        the spring-forward transition.  This walks the boundary.
        """
        cursor = datetime(2026, 3, 6, 12, 0, tzinfo=timezone.utc)
        seen = []
        for _ in range(5):
            cursor = next_daily_occurrence(cursor, "America/New_York", time(8, 0))
            seen.append(to_local(cursor, "America/New_York").strftime("%Y-%m-%d %H:%M"))
        # Local wall-clock is 08:00 on every single day, including both sides
        # of the transition.
        assert all(value.endswith("08:00") for value in seen), seen

    def test_occurrence_is_always_strictly_in_the_future(self):
        now = datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc)  # exactly 08:00 EST
        nxt = next_daily_occurrence(now, "America/New_York", time(8, 0))
        assert nxt > now

    def test_rejects_naive_after(self):
        with pytest.raises(ValueError):
            next_daily_occurrence(
                datetime(2026, 1, 1, 8, 0), "America/New_York", time(8, 0)
            )


class TestWeeklyRecurrence:
    def test_lands_on_the_requested_weekday(self):
        now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)  # a Thursday
        nxt = next_weekly_occurrence(
            now, "Asia/Kolkata", time(9, 0), weekday=0
        )  # Monday
        local = to_local(nxt, "Asia/Kolkata")
        assert local.weekday() == 0
        assert local.strftime("%H:%M") == "09:00"

    def test_advances_by_the_interval(self):
        now = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
        first = next_weekly_occurrence(now, "UTC", time(9, 0), weekday=0, interval=1)
        second = next_weekly_occurrence(
            first, "UTC", time(9, 0), weekday=0, interval=2
        )
        assert (second - first).days == 14

    def test_rejects_out_of_range_weekday(self):
        now = datetime(2026, 1, 5, tzinfo=timezone.utc)
        with pytest.raises(ValueError):
            next_weekly_occurrence(now, "UTC", time(9, 0), weekday=7)

    def test_rejects_zero_interval(self):
        now = datetime(2026, 1, 5, tzinfo=timezone.utc)
        with pytest.raises(ValueError):
            next_weekly_occurrence(now, "UTC", time(9, 0), weekday=0, interval=0)


# ── Rendering helpers ────────────────────────────────────────────────────────


class TestRendering:
    def test_describe_occurrence_includes_offset(self):
        instant = local_naive_to_utc(
            datetime(2026, 1, 15, 8, 0), "America/New_York"
        )
        rendered = __import__(
            "app.core.timezones", fromlist=["describe_occurrence"]
        ).describe_occurrence(instant, "America/New_York")
        assert "2026-01-15 08:00" in rendered
        assert "-0500" in rendered  # the offset, so DST is visible

    def test_utcnow_is_aware(self):
        assert utcnow().tzinfo is not None
        assert utcnow().utcoffset().total_seconds() == 0
