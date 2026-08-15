"""Mutable driver clocks, and the duty events the planner emits.

Every clock a driver has to satisfy lives on ``DriverState``. Keeping it all in
one object (rather than in loose locals inside the planner) is what makes
two-driver/team support additive later: a team is two ``DriverState`` instances
plus swap logic, not a rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .rules import (
    BREAK_AFTER_DRIVING_MINUTES,
    CYCLE_DAYS,
    CYCLE_MINUTES,
    MAX_DRIVING_MINUTES,
    MAX_WINDOW_MINUTES,
    MINUTES_PER_DAY,
    DutyStatus,
)


@dataclass
class DutyEvent:
    """One continuous block of a single duty status.

    The planner emits these back-to-back with no gaps and no overlaps; that
    contiguity is what lets the log sheets total exactly 24 hours per day.
    """

    status: DutyStatus
    start: datetime
    end: datetime
    location: str = ""
    note: str = ""
    miles: float = 0.0

    @property
    def minutes(self) -> int:
        return round((self.end - self.start).total_seconds() / 60)


@dataclass
class DriverState:
    """Every clock the driver must satisfy at once."""

    clock: datetime

    #: Hours already worked against the 70-hour cycle before this trip began,
    #: as entered by the driver ("Current Cycle Used").
    carried_in_minutes: int = 0

    #: Driving minutes accrued in the current 14-hour window (against the 11).
    driving_minutes: int = 0

    #: Start of the current 14-hour window; ``None`` means no shift is open yet,
    #: so the next on-duty event will open one.
    window_start: datetime | None = None

    #: Driving minutes since the last non-driving block of >= 30 consecutive
    #: minutes (against the 8-hour break trigger).
    driving_since_break: int = 0

    miles_since_fuel: float = 0.0

    #: On-duty minutes (driving + on-duty-not-driving) attributed per calendar
    #: day, used for the rolling 8-day cycle total.
    on_duty_by_day: dict[date, int] = field(default_factory=dict)

    #: Date the trip started, used to age out ``carried_in_minutes``.
    trip_start_date: date | None = None

    def __post_init__(self) -> None:
        if self.trip_start_date is None:
            self.trip_start_date = self.clock.date()

    # -- Cycle (70 hours / 8 rolling days) ----------------------------------

    def cycle_used(self) -> int:
        """On-duty minutes counting against the 70-hour limit right now.

        The rolling window is the current day plus the seven before it. We know
        the *total* the driver carried in but not how it was distributed across
        their prior days, so we hold it for the full 8-day window and only age
        it out once the trip itself has run that long. That is the conservative
        reading -- it can never under-report the driver's cycle usage.
        """
        window_start = self.clock.date() - timedelta(days=CYCLE_DAYS - 1)
        accrued = sum(
            minutes
            for day, minutes in self.on_duty_by_day.items()
            if day >= window_start
        )
        carried = self.carried_in_minutes
        if self.clock.date() >= self.trip_start_date + timedelta(days=CYCLE_DAYS):
            carried = 0
        return accrued + carried

    def cycle_remaining(self) -> int:
        return max(0, CYCLE_MINUTES - self.cycle_used())

    # -- Shift clocks (11 and 14) -------------------------------------------

    def driving_remaining(self) -> int:
        return max(0, MAX_DRIVING_MINUTES - self.driving_minutes)

    def window_remaining(self) -> int:
        """Minutes left in the 14-hour driving window.

        Returns the full window when no shift is open -- the window has not
        started yet, so nothing has been spent.
        """
        if self.window_start is None:
            return MAX_WINDOW_MINUTES
        elapsed = round((self.clock - self.window_start).total_seconds() / 60)
        return max(0, MAX_WINDOW_MINUTES - elapsed)

    def break_remaining(self) -> int:
        """Driving minutes available before the 30-minute break is required."""
        return max(0, BREAK_AFTER_DRIVING_MINUTES - self.driving_since_break)

    # -- Mutations -----------------------------------------------------------

    def record_on_duty(self, start: datetime, minutes: int) -> None:
        """Attribute on-duty minutes to calendar days, splitting at midnight.

        A shift that straddles midnight has to land partly on each day's sheet,
        otherwise the rolling cycle total drifts.
        """
        remaining = minutes
        cursor = start
        while remaining > 0:
            next_midnight = datetime.combine(
                cursor.date() + timedelta(days=1), datetime.min.time()
            ).replace(tzinfo=cursor.tzinfo)
            until_midnight = round((next_midnight - cursor).total_seconds() / 60)
            chunk = min(remaining, until_midnight or MINUTES_PER_DAY)
            self.on_duty_by_day[cursor.date()] = (
                self.on_duty_by_day.get(cursor.date(), 0) + chunk
            )
            cursor += timedelta(minutes=chunk)
            remaining -= chunk

    def apply_reset(self) -> None:
        """A 10+ hour off-duty period restores the 11- and 14-hour clocks."""
        self.driving_minutes = 0
        self.driving_since_break = 0
        self.window_start = None

    def apply_restart(self) -> None:
        """A 34+ hour off-duty period additionally zeroes the 70-hour cycle."""
        self.apply_reset()
        self.carried_in_minutes = 0
        self.on_duty_by_day.clear()
