"""Request validation and the computed trip payload.

Daily log sheets are rebuilt from the stored duty events on every read rather
than persisted, so they cannot fall out of step with the timeline.
"""

from __future__ import annotations

from rest_framework import serializers

from hos.logsheet import build_log_sheets
from hos.planner import FORCED_STOP_MARKER
from hos.rules import BREAK_MINUTES, DutyStatus
from hos.state import DutyEvent as EngineEvent
from routing.facilities import DEFAULT_LOOKBACK_MILES
from routing.geometry import cumulative_miles, point_at_fraction, simplify
from routing.trucks import STANDARD_DRY_VAN

from .models import Trip
from .stops import PARKABLE_KINDS, stop_kind as _stop_kind


def _latitude():
    return serializers.FloatField(
        required=False, allow_null=True, min_value=-90, max_value=90
    )


def _longitude():
    return serializers.FloatField(
        required=False, allow_null=True, min_value=-180, max_value=180
    )


class TripInputSerializer(serializers.Serializer):
    """The four inputs from the assessment brief, plus optional coordinates.

    A location picked from the search dropdown or pinned on the map arrives
    already resolved, so its coordinates come with it and no geocoding is
    needed. The text field stays required either way -- it carries the label
    that lands in the log sheet's Remarks column.
    """

    current_location = serializers.CharField(max_length=255, trim_whitespace=True)
    pickup_location = serializers.CharField(max_length=255, trim_whitespace=True)
    dropoff_location = serializers.CharField(max_length=255, trim_whitespace=True)
    current_cycle_used = serializers.FloatField(min_value=0, max_value=70)

    #: Optional; defaults to now. Present so tests and demos are reproducible.
    start_time = serializers.DateTimeField(required=False, allow_null=True)

    current_lat = _latitude()
    current_lon = _longitude()
    pickup_lat = _latitude()
    pickup_lon = _longitude()
    dropoff_lat = _latitude()
    dropoff_lon = _longitude()

    def validate_current_cycle_used(self, value: float) -> float:
        if value >= 70:
            raise serializers.ValidationError(
                "A driver at 70 hours cannot drive at all until they take a "
                "34-hour restart. Enter a value below 70."
            )
        return value

    def validate(self, attrs: dict) -> dict:
        """A half-supplied coordinate pair is a client bug, not a fallback.

        Silently geocoding the text instead would place the trip somewhere the
        driver did not pick, which is worse than refusing.
        """
        for field in ("current", "pickup", "dropoff"):
            has_lat = attrs.get(f"{field}_lat") is not None
            has_lon = attrs.get(f"{field}_lon") is not None
            if has_lat != has_lon:
                raise serializers.ValidationError(
                    {
                        f"{field}_location": (
                            "Latitude and longitude must be given together."
                        )
                    }
                )
        return attrs


class ForcedStopSerializer(serializers.Serializer):
    """One stop the driver wants moved earlier."""

    route_miles = serializers.FloatField(min_value=0)
    kind = serializers.ChoiceField(choices=sorted(PARKABLE_KINDS))


class ReplanSerializer(serializers.Serializer):
    forced_stops = ForcedStopSerializer(many=True, allow_empty=False)


def _to_engine_events(trip: Trip) -> list[EngineEvent]:
    return [
        EngineEvent(
            status=DutyStatus(event.status),
            start=event.start_at,
            end=event.end_at,
            location=event.location,
            note=event.note,
            miles=event.miles,
        )
        for event in trip.events.all()
    ]


#: Candidates offered under one stop. Five is as many as a driver will read.
MAX_FACILITIES_PER_STOP = 5


class TripSerializer(serializers.ModelSerializer):
    """The full computed payload returned for a trip."""

    inputs = serializers.SerializerMethodField()
    route = serializers.SerializerMethodField()
    summary = serializers.SerializerMethodField()
    timeline = serializers.SerializerMethodField()
    daily_logs = serializers.SerializerMethodField()

    class Meta:
        model = Trip
        fields = [
            "id",
            "created_at",
            "inputs",
            "route",
            "summary",
            "timeline",
            "daily_logs",
            "forced_stops",
            "replanned_from",
        ]

    # -- Cached derivations --------------------------------------------------

    def _events(self, trip: Trip) -> list[EngineEvent]:
        cache = self.context.setdefault("_events", {})
        if trip.pk not in cache:
            cache[trip.pk] = _to_engine_events(trip)
        return cache[trip.pk]

    def _sheets(self, trip: Trip):
        cache = self.context.setdefault("_sheets", {})
        if trip.pk not in cache:
            cache[trip.pk] = build_log_sheets(self._events(trip))
        return cache[trip.pk]

    # -- Fields --------------------------------------------------------------

    def get_inputs(self, trip: Trip) -> dict:
        return {
            "current_location": trip.current_location,
            "pickup_location": trip.pickup_location,
            "dropoff_location": trip.dropoff_location,
            "current_cycle_used": float(trip.current_cycle_used),
            "start_time": trip.start_time.isoformat(),
        }

    def get_route(self, trip: Trip) -> dict:
        # Simplified for drawing only -- the full-resolution geometry stays in
        # the database, because stop positions are interpolated from it.
        # Leaflet wants [lat, lon]; the stored GeoJSON is [lon, lat].
        drawable = simplify(trip.route_geometry or [])
        path = [[round(point[1], 5), round(point[0], 5)] for point in drawable]
        return {
            "total_miles": round(trip.total_distance_miles, 1),
            "total_drive_hours": round(trip.total_duration_minutes / 60, 2),
            "current_label": trip.current_label,
            "pickup_label": trip.pickup_label,
            "dropoff_label": trip.dropoff_label,
            "distances_estimated": trip.distances_estimated,
            "truck_routed": trip.truck_routed,
            "snapped_waypoints": trip.snapped_waypoints or [],
            "truck_spec": STANDARD_DRY_VAN.describe(),
            "no_road_route": trip.no_road_route,
            "path": path,
            "waypoints": [
                {
                    "kind": kind,
                    "label": label,
                    "lat": lat,
                    "lon": lon,
                }
                for kind, label, lat, lon in (
                    ("start", trip.current_label, trip.current_lat, trip.current_lon),
                    ("pickup", trip.pickup_label, trip.pickup_lat, trip.pickup_lon),
                    ("dropoff", trip.dropoff_label, trip.dropoff_lat, trip.dropoff_lon),
                )
                if lat is not None and lon is not None
            ],
        }

    def get_summary(self, trip: Trip) -> dict:
        events = self._events(trip)
        sheets = self._sheets(trip)

        driving = sum(e.minutes for e in events if e.status is DutyStatus.DRIVING)
        on_duty_not_driving = sum(
            e.minutes for e in events if e.status is DutyStatus.ON_DUTY
        )
        cycle_start = float(trip.current_cycle_used) * 60
        cycle_end = cycle_start + driving + on_duty_not_driving

        counts: dict[str, int] = {}
        for event in events:
            kind = _stop_kind(event.status.value, event.note)
            if kind in {"fuel", "break", "reset", "restart"}:
                counts[kind] = counts.get(kind, 0) + 1

        restarts = counts.get("restart", 0)
        return {
            "total_days": len(sheets),
            "total_driving_hours": round(driving / 60, 2),
            "total_on_duty_hours": round((driving + on_duty_not_driving) / 60, 2),
            "trip_duration_hours": round(
                sum(e.minutes for e in events) / 60, 2
            ),
            "cycle_used_at_start": round(cycle_start / 60, 2),
            # A 34-hour restart zeroes the cycle, so only the work done since
            # the last one still counts against the 70.
            "cycle_used_at_end": round(
                (cycle_end if not restarts else _cycle_after_last_restart(events)) / 60, 2
            ),
            "fuel_stops": counts.get("fuel", 0),
            "required_breaks": counts.get("break", 0),
            "required_rests": counts.get("reset", 0),
            "restarts": restarts,
            "starts_at": events[0].start.isoformat() if events else None,
            "ends_at": events[-1].end.isoformat() if events else None,
        }

    def get_timeline(self, trip: Trip) -> list[dict]:
        """The whole trip in order: driving legs and stops, interleaved.

        Driving is included deliberately. A list of stops alone reads as though
        they happen back to back, which makes a legal plan look nonsensical --
        the driving between them is what the stops are punctuating.
        """
        geometry = trip.route_geometry or []
        running = cumulative_miles(geometry) if len(geometry) > 1 else None
        total_miles = trip.total_distance_miles or 0.0
        travelled = 0.0

        timeline = []
        for event in self._events(trip):
            driving = event.status is DutyStatus.DRIVING
            kind = "driving" if driving else _stop_kind(event.status.value, event.note)

            # A mid-route stop has no address, but we know how far along the
            # route it happened, so its position is found by walking the
            # geometry that far. Stops are placed where the driver *is* when
            # they stop, i.e. at the distance covered so far.
            point = (
                point_at_fraction(geometry, travelled / total_miles, running)
                if running and total_miles > 0
                else None
            )
            if driving:
                travelled += event.miles

            timeline.append(
                {
                    "kind": kind,
                    "lat": round(point[1], 6) if point else None,
                    "lon": round(point[0], 6) if point else None,
                    "status": event.status.value,
                    "label": (
                        f"Drive {event.miles:,.0f} mi"
                        if driving
                        else event.note or event.status.value.replace("_", " ").title()
                    ),
                    "location": event.location,
                    "start": event.start.isoformat(),
                    "end": event.end.isoformat(),
                    "duration_hours": round(event.minutes / 60, 2),
                    "miles": round(event.miles, 1),
                    # Any non-driving block of 30+ consecutive minutes satisfies
                    # the required break, whichever duty status it is logged
                    # under. Surfacing this explains why a compliant trip can
                    # show zero standalone breaks.
                    "satisfies_break": (
                        not driving and event.minutes >= BREAK_MINUTES and kind != "break"
                    ),
                    # The driver put this one here, not the regulation. Without
                    # it the timeline shows a moved rest identically to an
                    # automatic one, and the whole point of moving it is lost.
                    "moved_by_driver": FORCED_STOP_MARKER in event.note,
                    # Where the driver could actually put the truck. Empty for
                    # driving legs, for the shipper and receiver (both real
                    # addresses), and whenever Overpass had nothing or was down.
                    "facilities": (
                        _facilities_before(trip.facilities, travelled)
                        if kind in PARKABLE_KINDS
                        else []
                    ),
                }
            )
        return timeline

    def get_daily_logs(self, trip: Trip) -> list[dict]:
        return [
            {
                "date": sheet.day.isoformat(),
                "total_miles": sheet.total_miles,
                "totals": sheet.totals_hours,
                "total_hours": sheet.total_hours,
                "segments": [
                    {
                        "status": segment.status.value,
                        "start_hour": segment.start_hour,
                        "end_hour": segment.end_hour,
                        "location": segment.location,
                        "note": segment.note,
                        "miles": round(segment.miles, 1),
                    }
                    for segment in sheet.segments
                ],
                "remarks": [
                    {
                        "hour": remark.hour,
                        "location": remark.location,
                        "note": remark.note,
                    }
                    for remark in sheet.remarks
                ],
            }
            for sheet in self._sheets(trip)
        ]


def _facilities_before(facilities: list[dict], stop_miles: float) -> list[dict]:
    """Stopping places within reach of a stop, nearest to it first.

    Strictly at or before the marker. A facility further along the route would
    mean driving past the clock that forced the stop, so it is not an option
    however convenient it looks -- see the note in ``routing.facilities``.

    Each carries how far *before* the stop it sits, because that is the number
    the driver is actually trading: stop 24 miles early and the whole remaining
    plan shifts by that much.
    """
    within = [
        {**facility, "miles_before_stop": round(stop_miles - facility["route_miles"], 1)}
        for facility in facilities or []
        if 0 <= stop_miles - facility["route_miles"] <= DEFAULT_LOOKBACK_MILES
    ]
    within.sort(key=lambda facility: facility["miles_before_stop"])
    return within[:MAX_FACILITIES_PER_STOP]


def _cycle_after_last_restart(events: list[EngineEvent]) -> float:
    """On-duty minutes accrued since the most recent 34-hour restart."""
    total = 0
    for event in events:
        if "34-hour restart" in event.note:
            total = 0
        elif event.status in {DutyStatus.DRIVING, DutyStatus.ON_DUTY}:
            total += event.minutes
    return total


class TripListSerializer(serializers.ModelSerializer):
    """Compact representation for the recent-trips list."""

    class Meta:
        model = Trip
        fields = [
            "id",
            "created_at",
            "current_label",
            "pickup_label",
            "dropoff_label",
            "total_distance_miles",
        ]
