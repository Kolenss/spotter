"""Stops the driver moved earlier than the rules demand.

The engine is greedy: it drives until a clock binds, which is optimal for
arrival time and often useless in practice because that mile marker may have
nowhere to park. A driver moving a rest earlier is trading arrival time for a
place to actually sleep.

The property that matters throughout this file is that the trade is *only* a
trade. A forced stop may delay the arrival and may add stops, but it must never
be able to produce an illegal plan -- the mandatory checks still run first on
every iteration, and stopping early can only ever restore a clock sooner.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from hos.logsheet import build_log_sheets
from hos.planner import ForcedStop, Leg, plan_trip
from hos.rules import (
    CYCLE_MINUTES,
    MAX_DRIVING_MINUTES,
    MAX_WINDOW_MINUTES,
    MINUTES_PER_DAY,
    DutyStatus,
)

START = datetime(2026, 8, 13, 6, 0)


def leg(miles: float, mph: float = 55.0, origin="Origin", dest="Destination") -> Leg:
    return Leg(
        origin_label=origin,
        dest_label=dest,
        miles=miles,
        duration_minutes=round(miles / mph * 60),
    )


def driving_miles_before(events, event) -> float:
    """Route miles covered when ``event`` began."""
    total = 0.0
    for candidate in events:
        if candidate is event:
            return total
        if candidate.status is DutyStatus.DRIVING:
            total += candidate.miles
    raise AssertionError("event not in timeline")


def moved(events) -> list:
    return [e for e in events if "moved earlier" in e.note]


# -- It happens, and where it was asked for ----------------------------------


def test_a_forced_rest_is_taken_at_the_mile_it_was_asked_for():
    events = plan_trip(
        [leg(900)], START, forced_stops=[ForcedStop(route_miles=300.0, kind="reset")]
    )

    (rest,) = moved(events)
    assert rest.status is DutyStatus.SLEEPER_BERTH
    assert rest.minutes == 10 * 60
    assert driving_miles_before(events, rest) == pytest.approx(300.0, abs=1.0)


def test_the_note_says_the_driver_moved_it():
    """The timeline has to be able to explain why this stop is here.

    It also has to stay parseable by trips.stops.stop_kind, which reads the
    note back to decide the colour -- hence the "10-hour" marker surviving.
    """
    events = plan_trip(
        [leg(900)], START, forced_stops=[ForcedStop(route_miles=300.0, kind="reset")]
    )

    (rest,) = moved(events)
    assert "10-hour" in rest.note
    assert "moved earlier by the driver" in rest.note


@pytest.mark.parametrize(
    "kind,status,minutes",
    [
        ("reset", DutyStatus.SLEEPER_BERTH, 600),
        ("restart", DutyStatus.SLEEPER_BERTH, 34 * 60),
        ("break", DutyStatus.OFF_DUTY, 30),
        ("fuel", DutyStatus.ON_DUTY, 30),
    ],
)
def test_every_kind_of_stop_can_be_moved(kind, status, minutes):
    events = plan_trip(
        [leg(900)], START, forced_stops=[ForcedStop(route_miles=200.0, kind=kind)]
    )

    (stop,) = moved(events)
    assert stop.status is status
    assert stop.minutes == minutes


def test_moving_a_rest_earlier_restores_the_clocks_just_the_same():
    """An early 10-hour rest is a 10-hour rest. Same effect, different reason."""
    events = plan_trip(
        [leg(900)], START, forced_stops=[ForcedStop(route_miles=200.0, kind="reset")]
    )

    (rest,) = moved(events)
    after = events[events.index(rest) + 1]
    assert after.status is DutyStatus.DRIVING, "should be able to drive again at once"


# -- The plan stays legal ----------------------------------------------------


def _assert_legal(events):
    """No shift drives past 11 hours, and no window past 14."""
    driving = 0
    window_start = None
    for event in events:
        if event.status in {DutyStatus.OFF_DUTY, DutyStatus.SLEEPER_BERTH}:
            if event.minutes >= 10 * 60:
                driving = 0
                window_start = None
            continue
        if window_start is None:
            window_start = event.start
        if event.status is DutyStatus.DRIVING:
            driving += event.minutes
        assert driving <= MAX_DRIVING_MINUTES, f"drove {driving} min in one shift"
        span = round((event.end - window_start).total_seconds() / 60)
        if event.status is DutyStatus.DRIVING:
            assert span <= MAX_WINDOW_MINUTES, f"drove {span} min into the window"


@pytest.mark.parametrize("at", [50.0, 200.0, 430.0, 600.0, 875.0])
def test_the_remaining_plan_is_still_legal_wherever_the_stop_is_moved(at):
    events = plan_trip(
        [leg(900)], START, forced_stops=[ForcedStop(route_miles=at, kind="reset")]
    )

    _assert_legal(events)


def test_the_timeline_stays_gap_free():
    """Contiguity is what lets the log sheets total exactly 24 hours."""
    events = plan_trip(
        [leg(1200)],
        START,
        forced_stops=[ForcedStop(300.0, "reset"), ForcedStop(700.0, "fuel")],
    )

    for earlier, later in zip(events, events[1:]):
        assert earlier.end == later.start


def test_days_still_total_exactly_1440_minutes():
    events = plan_trip(
        [leg(1400)],
        START,
        carried_in_minutes=30 * 60,
        forced_stops=[ForcedStop(250.0, "reset"), ForcedStop(900.0, "reset")],
    )

    for sheet in build_log_sheets(events):
        assert sum(sheet.totals_minutes.values()) == MINUTES_PER_DAY


def test_the_full_distance_is_still_driven():
    events = plan_trip(
        [leg(500), leg(400)],
        START,
        forced_stops=[ForcedStop(300.0, "reset")],
    )

    driven = sum(e.miles for e in events if e.status is DutyStatus.DRIVING)
    assert driven == pytest.approx(900.0, abs=0.1)


# -- Interaction with the mandatory rules ------------------------------------


def test_a_mandatory_restart_still_wins_over_a_forced_stop():
    """A driver out of cycle must restart, whatever they asked for.

    Started at 69 of the 70 hours, so the cycle dies almost immediately. The
    34-hour restart is not optional and has to happen regardless.
    """
    events = plan_trip(
        [leg(900)],
        START,
        carried_in_minutes=69 * 60,
        forced_stops=[ForcedStop(route_miles=400.0, kind="reset")],
    )

    assert any(
        "34-hour restart" in e.note and "moved earlier" not in e.note for e in events
    ), "the mandatory restart must still be taken"
    _assert_legal(events)


def test_moving_a_stop_never_beats_the_greedy_plan_on_arrival():
    """Stopping early costs time. That is the trade being made visible.

    If this ever came out faster, the greedy planner would have a bug -- it is
    supposed to already be optimal for arrival.
    """
    greedy = plan_trip([leg(900)], START)
    shifted = plan_trip(
        [leg(900)], START, forced_stops=[ForcedStop(route_miles=300.0, kind="reset")]
    )

    assert shifted[-1].end >= greedy[-1].end


def test_a_stop_beyond_the_route_is_simply_never_reached():
    greedy = plan_trip([leg(400)], START)
    shifted = plan_trip(
        [leg(400)], START, forced_stops=[ForcedStop(route_miles=5000.0, kind="reset")]
    )

    assert moved(shifted) == []
    assert [e.note for e in shifted] == [e.note for e in greedy]


def test_a_stop_at_mile_zero_is_ignored():
    """Nothing to move: the driver has not gone anywhere yet."""
    events = plan_trip(
        [leg(400)], START, forced_stops=[ForcedStop(route_miles=0.0, kind="reset")]
    )

    assert moved(events) == []


def test_stops_are_taken_in_order_wherever_they_are_listed():
    events = plan_trip(
        [leg(1200)],
        START,
        forced_stops=[ForcedStop(800.0, "fuel"), ForcedStop(300.0, "reset")],
    )

    order = [e.note for e in moved(events)]
    assert "10-hour" in order[0], "the 300 mi stop comes first"
    assert "Fuel" in order[1]


def test_a_forced_stop_can_span_two_legs_of_the_trip():
    """Mile markers are trip-wide, so one can land on the second leg.

    This is the case the leg-local loop would get wrong: without threading the
    running total, mile 700 of a 500+400 trip would never be recognised.
    """
    events = plan_trip(
        [leg(500), leg(400)],
        START,
        forced_stops=[ForcedStop(route_miles=700.0, kind="reset")],
    )

    (rest,) = moved(events)
    assert driving_miles_before(events, rest) == pytest.approx(700.0, abs=1.0)
