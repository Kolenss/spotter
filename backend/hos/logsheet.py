"""Turn a duty timeline into one DOT daily log sheet per calendar day.

A log sheet covers one calendar day, midnight to midnight, in the driver's home
terminal time zone (Sec. 395.8). Events that straddle midnight are split so each
day's four status totals add up to exactly 24 hours -- a sheet that does not
total 24 is not a valid sheet, so that invariant is asserted here rather than
left to the caller to notice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from itertools import groupby

from .rules import GRID_ROWS, MINUTES_PER_DAY, DutyStatus
from .state import DutyEvent


@dataclass
class LogSegment:
    """One drawn line on a single day's grid."""

    status: DutyStatus
    start_hour: float  # 0.0 - 24.0, hours from midnight
    end_hour: float
    location: str
    note: str
    miles: float


@dataclass
class LogRemark:
    """A duty-status change, annotated with where it happened."""

    hour: float
    location: str
    note: str


@dataclass
class LogSheet:
    day: date
    segments: list[LogSegment] = field(default_factory=list)
    remarks: list[LogRemark] = field(default_factory=list)
    #: Exact per-status totals in whole minutes; always sums to 1440.
    totals_minutes: dict[DutyStatus, int] = field(default_factory=dict)
    total_miles: float = 0.0

    @property
    def totals_hours(self) -> dict[str, float]:
        return {
            status.value: round(self.totals_minutes.get(status, 0) / 60.0, 2)
            for status in GRID_ROWS
        }

    @property
    def total_hours(self) -> float:
        """Always 24.0. Rendered on the sheet as the sum of the totals column."""
        return round(sum(self.totals_minutes.values()) / 60.0, 2)


def build_log_sheets(events: list[DutyEvent]) -> list[LogSheet]:
    """Slice a contiguous duty timeline into per-day log sheets."""
    if not events:
        return []

    daily = _pad_to_whole_days(_split_at_midnight(events))

    sheets: list[LogSheet] = []
    for day, group in groupby(daily, key=lambda event: event.start.date()):
        sheets.append(_build_sheet(day, list(group)))
    return sheets


def _split_at_midnight(events: list[DutyEvent]) -> list[DutyEvent]:
    split: list[DutyEvent] = []
    for event in events:
        cursor = event.start
        while cursor < event.end:
            boundary = min(event.end, _midnight_after(cursor))
            share = (boundary - cursor) / (event.end - event.start)
            split.append(
                DutyEvent(
                    status=event.status,
                    start=cursor,
                    end=boundary,
                    location=event.location,
                    note=event.note,
                    miles=event.miles * share,
                )
            )
            cursor = boundary
    return split


def _pad_to_whole_days(events: list[DutyEvent]) -> list[DutyEvent]:
    """Fill the head of the first day and the tail of the last with off duty.

    The driver is off duty before the trip begins and after it ends; without
    these blocks the first and last sheets would not total 24 hours.
    """
    padded = list(events)

    first = padded[0]
    day_start = _midnight_of(first.start)
    if first.start > day_start:
        padded.insert(
            0,
            DutyEvent(
                status=DutyStatus.OFF_DUTY,
                start=day_start,
                end=first.start,
                location=first.location,
                note="Off duty",
            ),
        )

    last = padded[-1]
    day_end = _midnight_after(last.end) if last.end != _midnight_of(last.end) else last.end
    if last.end < day_end:
        padded.append(
            DutyEvent(
                status=DutyStatus.OFF_DUTY,
                start=last.end,
                end=day_end,
                location=last.location,
                note="Off duty",
            )
        )
    return padded


def _build_sheet(day: date, events: list[DutyEvent]) -> LogSheet:
    sheet = LogSheet(day=day)
    totals = {status: 0 for status in GRID_ROWS}

    for event in events:
        start_hour = _hours_from_midnight(event.start, day)
        end_hour = _hours_from_midnight(event.end, day)
        totals[event.status] += event.minutes
        sheet.total_miles += event.miles
        sheet.segments.append(
            LogSegment(
                status=event.status,
                start_hour=start_hour,
                end_hour=end_hour,
                location=event.location,
                note=event.note,
                miles=event.miles,
            )
        )
        # Sec. 395.8 requires the city/state at every change of duty status.
        # Hour 0 is either the day's start or a block carried over midnight --
        # neither is a change.
        if start_hour > 0:
            sheet.remarks.append(
                LogRemark(
                    hour=start_hour,
                    location=event.location,
                    note=event.note or _default_note(event.status),
                )
            )

    sheet.totals_minutes = totals
    sheet.total_miles = round(sheet.total_miles, 1)

    accounted = sum(totals.values())
    if accounted != MINUTES_PER_DAY:
        raise ValueError(
            f"log sheet for {day} totals {accounted} minutes, expected "
            f"{MINUTES_PER_DAY} (24 hours)"
        )
    return sheet


def _default_note(status: DutyStatus) -> str:
    return {
        DutyStatus.OFF_DUTY: "Off duty",
        DutyStatus.SLEEPER_BERTH: "Sleeper berth",
        DutyStatus.DRIVING: "Driving",
        DutyStatus.ON_DUTY: "On duty (not driving)",
    }[status]


def _midnight_of(moment: datetime) -> datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def _midnight_after(moment: datetime) -> datetime:
    return _midnight_of(moment) + timedelta(days=1)


def _hours_from_midnight(moment: datetime, day: date) -> float:
    delta = moment - datetime.combine(day, datetime.min.time()).replace(
        tzinfo=moment.tzinfo
    )
    return round(delta.total_seconds() / 3600.0, 4)
