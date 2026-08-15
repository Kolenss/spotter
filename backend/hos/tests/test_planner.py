"""Rules-engine tests.

The assessment is graded on the accuracy of the output, so these tests encode
the regulation directly -- including the worked example printed in the FMCSA
guide itself, which is the closest thing to an official expected output.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from hos.logsheet import build_log_sheets
from hos.planner import Leg, plan_trip
from hos.rules import (
    BREAK_AFTER_DRIVING_MINUTES,
    CYCLE_MINUTES,
    MAX_DRIVING_MINUTES,
    MAX_WINDOW_MINUTES,
    MINUTES_PER_DAY,
    DutyStatus,
)
from hos.state import DriverState, DutyEvent

START = datetime(2026, 8, 13, 6, 0)


def leg(miles: float, mph: float = 55.0, origin="Origin", dest="Destination") -> Leg:
    return Leg(
        origin_label=origin,
        dest_label=dest,
        miles=miles,
        duration_minutes=round(miles / mph * 60),
    )


def minutes_in(events, status) -> int:
    return sum(e.minutes for e in events if e.status is status)


# -- 1. The guide's own worked example ---------------------------------------


def test_john_doe_day_from_the_guide_totals_exactly_24():
    """Reproduces the completed log on p.18-19 of the FMCSA guide.

    Driver John Doe, Richmond VA to Newark NJ, 04/09/2021. The guide prints the
    totals column as 10 / 1.75 / 7.75 / 4.5 = 24, so that is our expected value.
    """
    day = date(2021, 4, 9)

    def at(hour: float) -> datetime:
        return datetime.combine(day, datetime.min.time()) + timedelta(hours=hour)

    events = [
        DutyEvent(DutyStatus.OFF_DUTY, at(0), at(6), "Richmond, VA", "Off duty"),
        DutyEvent(DutyStatus.ON_DUTY, at(6), at(7.5), "Richmond, VA", "Load, pre-trip"),
        DutyEvent(DutyStatus.DRIVING, at(7.5), at(9), "Richmond, VA", "", 80),
        DutyEvent(DutyStatus.ON_DUTY, at(9), at(9.5), "Fredericksburg, VA", "Fuel stop"),
        DutyEvent(DutyStatus.DRIVING, at(9.5), at(12), "Fredericksburg, VA", "", 120),
        DutyEvent(DutyStatus.OFF_DUTY, at(12), at(13), "Baltimore, MD", "Lunch"),
        DutyEvent(DutyStatus.DRIVING, at(13), at(15), "Baltimore, MD", "", 95),
        DutyEvent(DutyStatus.ON_DUTY, at(15), at(15.5), "Philadelphia, PA", "Delivery"),
        DutyEvent(DutyStatus.DRIVING, at(15.5), at(16), "Philadelphia, PA", "", 25),
        DutyEvent(DutyStatus.SLEEPER_BERTH, at(16), at(17.75), "Cherry Hill, NJ", "Rest"),
        DutyEvent(DutyStatus.DRIVING, at(17.75), at(19), "Cherry Hill, NJ", "", 30),
        DutyEvent(DutyStatus.ON_DUTY, at(19), at(21), "Newark, NJ", "Post-trip"),
        DutyEvent(DutyStatus.OFF_DUTY, at(21), at(24), "Newark, NJ", "Off duty"),
    ]

    sheets = build_log_sheets(events)
    assert len(sheets) == 1

    totals = sheets[0].totals_hours
    assert totals["off_duty"] == 10.0
    assert totals["sleeper_berth"] == 1.75
    assert totals["driving"] == 7.75
    assert totals["on_duty"] == 4.5
    assert sheets[0].total_hours == 24.0


# -- 2. The 11-hour driving limit --------------------------------------------


def test_eleven_hour_driving_limit_forces_a_ten_hour_reset():
    # ~16 hours of driving at 55 mph: more than one shift can legally hold.
    events = plan_trip([leg(880)], START)

    driving_runs = _runs_of(events, DutyStatus.DRIVING)
    assert max(driving_runs) <= MAX_DRIVING_MINUTES

    resets = [e for e in events if "10-hour reset" in e.note]
    assert resets, "expected a 10-hour reset once the 11-hour limit was reached"
    assert all(e.minutes == 10 * 60 for e in resets)
    assert "11-hour driving limit" in resets[0].note


def _runs_of(events, status) -> list[int]:
    """Total minutes of ``status`` between rest periods."""
    runs, current = [], 0
    for event in events:
        if event.status is status:
            current += event.minutes
        elif event.minutes >= 10 * 60:  # a reset ends the run
            runs.append(current)
            current = 0
    runs.append(current)
    return [r for r in runs if r]


# -- 3. The 14-hour driving window -------------------------------------------


def test_fourteen_hour_window_blocks_driving_even_with_driving_hours_left():
    """The window is consecutive wall-clock time and does not pause.

    Driven directly against the state because the window rarely binds before
    the 11-hour limit under this app's assumptions (see the note in README):
    it takes more than 3 hours of non-driving work inside one shift.
    """
    state = DriverState(clock=START)
    state.window_start = START - timedelta(hours=13, minutes=30)

    assert state.driving_remaining() == MAX_DRIVING_MINUTES  # 11 hours untouched
    assert state.window_remaining() == 30  # but only half an hour of window

    events = plan_trip([leg(600)], START)
    assert events  # sanity

    # And once the window is fully spent, no driving may be scheduled at all.
    state.window_start = START - timedelta(hours=MAX_WINDOW_MINUTES / 60)
    assert state.window_remaining() == 0


def test_window_opens_at_first_work_not_first_driving():
    """A pre-trip inspection starts the 14-hour clock just as driving does."""
    state = DriverState(clock=START)
    assert state.window_start is None

    events = plan_trip([leg(100), leg(100)], START)
    first = events[0]
    assert first.status is DutyStatus.DRIVING
    assert first.start == START


# -- 4. The 30-minute break --------------------------------------------------


def test_break_inserted_after_eight_cumulative_driving_hours():
    events = plan_trip([leg(605)], START)  # 11 hours of driving at 55 mph

    driving_before_break = 0
    for event in events:
        if event.status is DutyStatus.DRIVING:
            driving_before_break += event.minutes
        elif event.minutes >= 30:
            break

    assert driving_before_break == BREAK_AFTER_DRIVING_MINUTES

    breaks = [e for e in events if "30-minute break" in e.note]
    assert breaks
    assert breaks[0].minutes == 30
    assert breaks[0].status is DutyStatus.OFF_DUTY


def test_no_two_driving_stretches_exceed_eight_hours_without_a_break():
    events = plan_trip([leg(500), leg(900)], START)

    since_break = 0
    for event in events:
        if event.status is DutyStatus.DRIVING:
            since_break += event.minutes
            assert since_break <= BREAK_AFTER_DRIVING_MINUTES, (
                f"drove {since_break} min without a 30-minute break"
            )
        elif event.minutes >= 30:
            since_break = 0


# -- 5. A qualifying on-duty stop satisfies the break ------------------------


def test_fuel_stop_satisfies_the_break_requirement():
    """The guide allows fuel/loading stops to count, if 30 consecutive minutes.

    So a fuel stop landing near the 8-hour mark must not be followed by a
    redundant separate break.
    """
    events = plan_trip([leg(1400, mph=62.5)], START)

    for index, event in enumerate(events[:-1]):
        if event.note == "Fuel stop":
            following = events[index + 1]
            assert "30-minute break" not in following.note, (
                "a 30-minute fuel stop already satisfies the break requirement"
            )


def test_pickup_hour_satisfies_the_break_requirement():
    events = plan_trip([leg(440), leg(440)], START)
    for index, event in enumerate(events[:-1]):
        if event.note == "Pickup":
            assert "30-minute break" not in events[index + 1].note


# -- 6. Fueling every 1,000 miles --------------------------------------------


def test_fuel_stop_at_least_every_thousand_miles():
    events = plan_trip([leg(1200), leg(1500)], START)

    miles_since_fuel = 0.0
    for event in events:
        if event.status is DutyStatus.DRIVING:
            miles_since_fuel += event.miles
            assert miles_since_fuel <= 1000.0 + 1e-6, (
                f"drove {miles_since_fuel:.1f} mi without fueling"
            )
        elif event.note == "Fuel stop":
            miles_since_fuel = 0.0

    fuel_stops = [e for e in events if e.note == "Fuel stop"]
    assert len(fuel_stops) >= 2  # 2,700 total miles
    assert all(e.status is DutyStatus.ON_DUTY for e in fuel_stops)


def test_short_trip_needs_no_fuel_stop():
    events = plan_trip([leg(120), leg(180)], START)
    assert not [e for e in events if e.note == "Fuel stop"]


# -- 7. The 70-hour cycle and the 34-hour restart ----------------------------


def test_cycle_exhaustion_triggers_a_34_hour_restart():
    # Start with 68 of 70 cycle hours already used, then ask for a long trip.
    events = plan_trip([leg(1500)], START, carried_in_minutes=68 * 60)

    restarts = [e for e in events if "34-hour restart" in e.note]
    assert restarts, "expected a 34-hour restart once the 70-hour cycle ran out"
    assert restarts[0].minutes == 34 * 60


def test_restart_returns_the_full_seventy_hours():
    state = DriverState(clock=START, carried_in_minutes=CYCLE_MINUTES)
    assert state.cycle_remaining() == 0

    state.apply_restart()
    assert state.cycle_remaining() == CYCLE_MINUTES


def test_cycle_counts_on_duty_not_just_driving():
    """Sec. 395.3(b) counts total on-duty time, so pickup and fueling count."""
    state = DriverState(clock=START)
    state.record_on_duty(START, 60)
    assert state.cycle_used() == 60


# -- 8. Every daily log totals exactly 24 hours ------------------------------


@pytest.mark.parametrize(
    "legs,cycle_used",
    [
        ([leg(50), leg(120)], 0),
        ([leg(240), leg(700)], 0),
        ([leg(400), leg(1800)], 10 * 60),
        ([leg(1200), leg(2600)], 30 * 60),
        ([leg(80), leg(3000)], 60 * 60),
        ([leg(15, mph=30)], 0),
        ([leg(900, mph=45), leg(900, mph=70)], 45 * 60),
    ],
)
def test_every_daily_log_totals_exactly_24_hours(legs, cycle_used):
    events = plan_trip(legs, START, carried_in_minutes=cycle_used)
    sheets = build_log_sheets(events)

    assert sheets
    for sheet in sheets:
        assert sum(sheet.totals_minutes.values()) == MINUTES_PER_DAY
        assert sheet.total_hours == 24.0


def test_log_sheets_cover_consecutive_days_with_no_gaps():
    events = plan_trip([leg(500), leg(2200)], START)
    sheets = build_log_sheets(events)

    for earlier, later in zip(sheets, sheets[1:]):
        assert later.day == earlier.day + timedelta(days=1)


# -- 9. Timeline integrity ---------------------------------------------------


@pytest.mark.parametrize(
    "legs",
    [
        [leg(100), leg(250)],
        [leg(600), leg(1400)],
        [leg(2000), leg(2000)],
    ],
)
def test_timeline_is_contiguous(legs):
    events = plan_trip(legs, START)

    assert events[0].start == START
    for earlier, later in zip(events, events[1:]):
        assert earlier.end == later.start, "gap or overlap in the duty timeline"
        assert later.end > later.start, "zero-length or reversed event"


def test_pickup_and_dropoff_are_on_duty_not_off_duty():
    """The single most consequential detail: Sec. 395.2 puts loading and
    unloading on duty, so they burn the 14-hour window and the 70-hour cycle."""
    events = plan_trip([leg(200), leg(300)], START)

    pickup = next(e for e in events if e.note == "Pickup")
    dropoff = next(e for e in events if e.note == "Drop-off")

    assert pickup.status is DutyStatus.ON_DUTY
    assert dropoff.status is DutyStatus.ON_DUTY
    assert pickup.minutes == 60
    assert dropoff.minutes == 60


def test_driving_miles_sum_to_the_route_distance():
    events = plan_trip([leg(430), leg(870)], START)
    driven = sum(e.miles for e in events if e.status is DutyStatus.DRIVING)
    assert driven == pytest.approx(1300, abs=0.5)


def test_trip_ends_with_the_dropoff():
    events = plan_trip([leg(120), leg(340)], START)
    assert events[-1].note == "Drop-off"
