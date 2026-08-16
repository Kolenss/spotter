"""The HOS simulator.

Walks a trip forward in time and inserts the stops the regulations demand,
emitting a gap-free timeline of duty events.

The rules are enforced in priority order at every decision point:

1. 70-hour cycle exhausted     -> 34-hour restart
2. 11-hour or 14-hour spent    -> 10-hour reset
3. 8 cumulative driving hours  -> 30-minute break
4. 1,000 miles since fueling   -> fuel stop (on duty)

Otherwise the driver drives until whichever limit binds first.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from .rules import (
    BREAK_MINUTES,
    DEFAULT_SPEED_MPH,
    DROPOFF_MINUTES,
    FUEL_INTERVAL_MILES,
    FUEL_MINUTES,
    PICKUP_MINUTES,
    RESET_MINUTES,
    RESTART_MINUTES,
    DutyStatus,
)
from .state import DriverState, DutyEvent

#: Distances below this are treated as arrived; avoids float residue looping.
MILE_EPSILON = 1e-6

#: Backstop against a pathological input spinning the planner forever.
MAX_ITERATIONS = 10_000


@dataclass
class Leg:
    """One point-to-point driving segment, as returned by the routing layer."""

    origin_label: str
    dest_label: str
    miles: float
    duration_minutes: int

    def speed_mph(self) -> float:
        """Average speed implied by the route itself.

        Using the routing API's own duration is more faithful than a flat
        assumed speed -- it already accounts for terrain and road class.
        """
        if self.duration_minutes <= 0 or self.miles <= 0:
            return DEFAULT_SPEED_MPH
        return self.miles / (self.duration_minutes / 60.0)


def plan_trip(
    legs: Sequence[Leg],
    start_time: datetime,
    carried_in_minutes: int = 0,
) -> list[DutyEvent]:
    """Simulate the trip and return its contiguous duty-status timeline.

    ``legs`` is ordered current -> pickup -> dropoff. The 1-hour pickup is
    inserted after the first leg and the 1-hour dropoff after the last, both as
    on-duty-not-driving time.

    """
    state = DriverState(clock=start_time, carried_in_minutes=carried_in_minutes)
    events: list[DutyEvent] = []

    for index, leg in enumerate(legs):
        _drive_leg(state, events, leg)

        if index == 0 and len(legs) > 1:
            _on_duty(state, events, PICKUP_MINUTES, leg.dest_label, "Pickup")
        elif index == len(legs) - 1:
            _on_duty(state, events, DROPOFF_MINUTES, leg.dest_label, "Drop-off")

    return _merge_adjacent(events)


# -- Driving -----------------------------------------------------------------


def _drive_leg(
    state: DriverState,
    events: list[DutyEvent],
    leg: Leg,
) -> None:
    """Drive one leg to its end, inserting stops as clocks bind."""
    mph = leg.speed_mph()
    remaining = leg.miles
    iterations = 0

    while remaining > MILE_EPSILON:
        iterations += 1
        if iterations > MAX_ITERATIONS:  # pragma: no cover - safety valve
            raise RuntimeError(f"planner failed to converge on leg {leg!r}")

        here = _enroute_label(leg, leg.miles - remaining)

        # 1. The 70-hour cycle is spent: only a 34-hour restart frees it.
        if state.cycle_remaining() <= 0:
            _rest(
                state,
                events,
                RESTART_MINUTES,
                here,
                "34-hour restart (70-hour cycle exhausted)",
            )
            state.apply_restart()
            continue

        # 2. Out of driving hours, or the 14-hour window has closed.
        driving_spent = state.driving_remaining() <= 0
        window_closed = state.window_remaining() <= 0
        if driving_spent or window_closed:
            reason = (
                "10-hour reset (11-hour driving limit reached)"
                if driving_spent
                else "10-hour reset (14-hour driving window closed)"
            )
            _rest(state, events, RESET_MINUTES, here, reason)
            state.apply_reset()
            continue

        # 3. Eight cumulative driving hours since the last 30-minute break.
        if state.break_remaining() <= 0:
            _break(state, events, here)
            continue

        # 4. Due for fuel.
        minutes_until_fuel = math.floor(
            (FUEL_INTERVAL_MILES - state.miles_since_fuel) / mph * 60
        )
        if minutes_until_fuel <= 0:
            _fuel(state, events, here)
            continue

        minutes_for_rest_of_leg = math.ceil(remaining / mph * 60)
        budget = min(
            state.driving_remaining(),
            state.window_remaining(),
            state.break_remaining(),
            state.cycle_remaining(),
            minutes_until_fuel,
            minutes_for_rest_of_leg,
        )
        budget = max(1, budget)

        if budget >= minutes_for_rest_of_leg:
            chunk_miles = remaining
        else:
            chunk_miles = min(remaining, budget / 60.0 * mph)

        _advance(
            state,
            events,
            budget,
            DutyStatus.DRIVING,
            location=here,
            note="",
            miles=chunk_miles,
        )
        remaining -= chunk_miles


def _enroute_label(leg: Leg, miles_done: float) -> str:
    """Label for a stop that happens partway along a leg.

    Phase 1 has no map layer, so intermediate stops are described by their
    progress along the leg. When the map lands, the route geometry can be
    interpolated and reverse-geocoded to give the city/state that Sec. 395.8
    actually wants in the Remarks line.
    """
    if miles_done <= MILE_EPSILON:
        return leg.origin_label
    return f"En route to {leg.dest_label} ({miles_done:,.0f} mi)"


# -- Stop primitives ---------------------------------------------------------


def _on_duty(
    state: DriverState,
    events: list[DutyEvent],
    minutes: int,
    location: str,
    note: str,
) -> None:
    """On-duty-not-driving time: loading, unloading, fueling, paperwork.

    Burns the 14-hour window and the 70-hour cycle but not the 11-hour driving
    clock -- this distinction is the one most often gotten wrong.
    """
    _advance(state, events, minutes, DutyStatus.ON_DUTY, location, note)


def _fuel(state: DriverState, events: list[DutyEvent], location: str) -> None:
    _on_duty(state, events, FUEL_MINUTES, location, "Fuel stop")
    state.miles_since_fuel = 0.0


def _break(state: DriverState, events: list[DutyEvent], location: str) -> None:
    _advance(
        state,
        events,
        BREAK_MINUTES,
        DutyStatus.OFF_DUTY,
        location,
        "30-minute break (8 cumulative driving hours)",
    )


def _rest(
    state: DriverState,
    events: list[DutyEvent],
    minutes: int,
    location: str,
    note: str,
) -> None:
    _advance(state, events, minutes, DutyStatus.SLEEPER_BERTH, location, note)


def _advance(
    state: DriverState,
    events: list[DutyEvent],
    minutes: int,
    status: DutyStatus,
    location: str,
    note: str,
    miles: float = 0.0,
) -> None:
    """Emit one duty event and roll every clock forward by ``minutes``."""
    if minutes <= 0:
        return

    start = state.clock
    end = start + timedelta(minutes=minutes)
    events.append(
        DutyEvent(
            status=status,
            start=start,
            end=end,
            location=location,
            note=note,
            miles=miles,
        )
    )
    state.clock = end

    if status is DutyStatus.DRIVING or status is DutyStatus.ON_DUTY:
        # The 14-hour window opens at the first work of the shift, driving or
        # not -- a pre-trip inspection starts it just as driving would.
        if state.window_start is None:
            state.window_start = start
        state.record_on_duty(start, minutes)

    if status is DutyStatus.DRIVING:
        state.driving_minutes += minutes
        state.driving_since_break += minutes
        state.miles_since_fuel += miles
    elif minutes >= BREAK_MINUTES:
        # Any non-driving block of 30+ consecutive minutes satisfies the break,
        # whether it is off duty, sleeper berth, or on-duty work such as a fuel
        # stop or an unload. Short scattered gaps do not qualify.
        state.driving_since_break = 0


def _merge_adjacent(events: list[DutyEvent]) -> list[DutyEvent]:
    """Collapse consecutive events sharing a status and note into one block.

    The loop can emit several driving chunks back to back when two limits
    happen to bind at the same moment; on the grid those should read as a
    single line, not a row of hairline segments.
    """
    merged: list[DutyEvent] = []
    for event in events:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and previous.status is event.status
            and previous.note == event.note
            and previous.end == event.start
        ):
            previous.end = event.end
            previous.miles += event.miles
        else:
            merged.append(event)
    return merged
