"""FMCSA Hours-of-Service constants for property-carrying drivers.

Source: Interstate Truck Driver's Guide to Hours of Service, FMCSA, April 2022
(FMCSA-HOS-395-DRIVERS-GUIDE-TO-HOS), and 49 CFR Part 395.

All durations are expressed in whole minutes. The engine does every calculation
in integer minutes so that a day's four duty-status totals sum to exactly 1440
(24.00 hours) with no floating-point drift -- a log sheet whose totals do not
read exactly 24 is an invalid log sheet.
"""

from enum import Enum

# -- The three hard limits ---------------------------------------------------

#: Maximum driving time inside one 14-hour window. Sec. 395.3(a)(3)
MAX_DRIVING_MINUTES = 11 * 60

#: The driving window: 14 *consecutive* hours measured from the first work of
#: the shift. It does not pause for meals, fuel stops, or naps.
#: Sec. 395.3(a)(2)
MAX_WINDOW_MINUTES = 14 * 60

#: A 30-minute break from driving is required once this much *cumulative*
#: (not consecutive) driving time has accrued. Sec. 395.3(a)(3)(ii)
BREAK_AFTER_DRIVING_MINUTES = 8 * 60

#: Length of that break. It may be spent off duty, on duty (not driving), or in
#: the sleeper berth -- but it must be 30 consecutive minutes. Short
#: non-consecutive gaps cannot be added together to reach it.
BREAK_MINUTES = 30

#: The rolling cycle: 70 hours on duty in any 8 consecutive days. This counts
#: *total on-duty time* (driving plus on-duty-not-driving), not just driving.
#: Sec. 395.3(b)
CYCLE_MINUTES = 70 * 60
CYCLE_DAYS = 8

#: Consecutive off-duty time that restores the 11- and 14-hour clocks.
#: Sec. 395.3(a)(1)
RESET_MINUTES = 10 * 60

#: Consecutive off-duty time that returns the 70-hour cycle to zero.
#: Optional, not mandatory. Sec. 395.3(c)
RESTART_MINUTES = 34 * 60

# -- Assessment-supplied trip assumptions ------------------------------------

#: "Fueling at least once every 1,000 miles."
FUEL_INTERVAL_MILES = 1000.0

#: Duration of a fuel stop. The assessment does not specify one; 30 minutes is
#: our stated assumption. Fueling is on-duty-not-driving time because Sec. 395.2
#: counts "all time inspecting, servicing, or conditioning any truck, including
#: fueling it" as on duty. At 30 minutes it also satisfies the required break.
FUEL_MINUTES = 30

#: "1 hour for pickup and drop-off." Both are on-duty-not-driving: Sec. 395.2
#: counts "all time loading, unloading, supervising, or attending your truck"
#: as on-duty time.
PICKUP_MINUTES = 60
DROPOFF_MINUTES = 60

# -- Misc --------------------------------------------------------------------

#: Fallback average speed, used only when a route provides distance but no
#: usable duration. Real routes derive their own speed from the routing API.
DEFAULT_SPEED_MPH = 55.0

MINUTES_PER_DAY = 24 * 60


class DutyStatus(str, Enum):
    """The four rows of the DOT graph grid, in the order they are drawn."""

    OFF_DUTY = "off_duty"
    SLEEPER_BERTH = "sleeper_berth"
    DRIVING = "driving"
    ON_DUTY = "on_duty"


#: Statuses that accrue against the 70-hour cycle and the 14-hour window.
ON_DUTY_STATUSES = frozenset({DutyStatus.DRIVING, DutyStatus.ON_DUTY})

#: Row order for rendering and for the totals column on a log sheet.
GRID_ROWS = (
    DutyStatus.OFF_DUTY,
    DutyStatus.SLEEPER_BERTH,
    DutyStatus.DRIVING,
    DutyStatus.ON_DUTY,
)
