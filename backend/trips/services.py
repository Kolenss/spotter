"""Ties the routing layer and the HOS engine together and persists the result."""

from __future__ import annotations

import re
from datetime import datetime

from django.db import transaction

from hos.planner import ForcedStop, Leg, plan_trip
from hos.rules import DutyStatus
from routing.client import GeocodingError, Place, RouteLeg, geocode, reverse, route
from routing.facilities import find_along

from .models import DutyEvent, Trip
from .stops import PARKABLE_KINDS, stop_kind

__all__ = ["GeocodingError", "ReplanUnavailable", "build_trip", "replan_trip"]


@transaction.atomic
def build_trip(
    *,
    current_location: str,
    pickup_location: str,
    dropoff_location: str,
    current_cycle_used: float,
    start_time: datetime | None = None,
    current_lat: float | None = None,
    current_lon: float | None = None,
    pickup_lat: float | None = None,
    pickup_lon: float | None = None,
    dropoff_lat: float | None = None,
    dropoff_lon: float | None = None,
) -> Trip:
    """Resolve, route, plan and store a trip.

    Coordinates are optional per waypoint. When present -- the driver picked a
    search result or pinned the map -- that point is used as given and no
    geocoding happens for it.

    Raises ``GeocodingError`` if a location given as text alone cannot be
    resolved; the view turns that into a 400 with the message shown to the
    driver.
    """
    origin = _resolve(current_location, current_lat, current_lon)
    pickup = _resolve(pickup_location, pickup_lat, pickup_lon)
    dropoff = _resolve(dropoff_location, dropoff_lat, dropoff_lon)

    to_pickup = route(origin, pickup)
    to_dropoff = route(pickup, dropoff)

    # Trips are planned in home-terminal local time; see the TIME_ZONE note in
    # settings. Defaulting to "now" keeps the four documented inputs sufficient,
    # while an explicit start_time makes tests deterministic.
    start = (start_time or datetime.now()).replace(second=0, microsecond=0)

    events = plan_trip(
        legs=[
            Leg(
                origin_label=origin.label,
                dest_label=pickup.label,
                miles=to_pickup.miles,
                duration_minutes=to_pickup.duration_minutes,
            ),
            Leg(
                origin_label=pickup.label,
                dest_label=dropoff.label,
                miles=to_dropoff.miles,
                duration_minutes=to_dropoff.duration_minutes,
            ),
        ],
        start_time=start,
        carried_in_minutes=round(float(current_cycle_used) * 60),
    )

    geometry = _combined_geometry(to_pickup, to_dropoff)
    total_miles = round(to_pickup.miles + to_dropoff.miles, 1)

    trip = Trip.objects.create(
        current_location=current_location,
        pickup_location=pickup_location,
        dropoff_location=dropoff_location,
        current_cycle_used=current_cycle_used,
        start_time=start,
        current_label=origin.label,
        pickup_label=pickup.label,
        dropoff_label=dropoff.label,
        current_lat=origin.latitude,
        current_lon=origin.longitude,
        pickup_lat=pickup.latitude,
        pickup_lon=pickup.longitude,
        dropoff_lat=dropoff.latitude,
        dropoff_lon=dropoff.longitude,
        total_distance_miles=total_miles,
        total_duration_minutes=to_pickup.duration_minutes + to_dropoff.duration_minutes,
        route_geometry=geometry,
        legs=[
            {"miles": to_pickup.miles, "minutes": to_pickup.duration_minutes},
            {"miles": to_dropoff.miles, "minutes": to_dropoff.duration_minutes},
        ],
        facilities=[
            facility.as_dict()
            for facility in find_along(geometry, _parkable_miles(events), total_miles)
        ],
        distances_estimated=to_pickup.is_estimate or to_dropoff.is_estimate,
        # Both legs, not either: a trip is only truck-legal end to end if every
        # leg of it was routed for a truck.
        truck_routed=to_pickup.is_truck_legal and to_dropoff.is_truck_legal,
        no_road_route=to_pickup.is_unroutable or to_dropoff.is_unroutable,
    )

    _store_events(trip, events)
    return trip


class ReplanUnavailable(ValueError):
    """This trip cannot be re-planned, and retrying will not change that."""


@transaction.atomic
def replan_trip(trip: Trip, forced_stops: list[dict]) -> Trip:
    """Re-plan an existing trip with one or more stops moved earlier.

    Produces a **new** Trip rather than mutating the old one, so the driver can
    compare what moving the stop cost them. The route itself is reused wholesale
    -- same places, same geometry, same facilities, same leg distances -- which
    means this makes no network calls at all and is therefore both fast and
    incapable of returning different mileage than the plan it is compared with.
    """
    if not trip.legs:
        raise ReplanUnavailable(
            "This trip was planned before re-planning existed, so its per-leg "
            "distances were not recorded. Plan it again to move its stops."
        )

    stops = [
        ForcedStop(route_miles=float(stop["route_miles"]), kind=stop["kind"])
        for stop in forced_stops
    ]

    events = plan_trip(
        legs=[
            Leg(
                origin_label=trip.current_label,
                dest_label=trip.pickup_label,
                miles=trip.legs[0]["miles"],
                duration_minutes=trip.legs[0]["minutes"],
            ),
            Leg(
                origin_label=trip.pickup_label,
                dest_label=trip.dropoff_label,
                miles=trip.legs[1]["miles"],
                duration_minutes=trip.legs[1]["minutes"],
            ),
        ],
        start_time=trip.start_time,
        carried_in_minutes=round(float(trip.current_cycle_used) * 60),
        forced_stops=stops,
    )

    replan = Trip.objects.create(
        current_location=trip.current_location,
        pickup_location=trip.pickup_location,
        dropoff_location=trip.dropoff_location,
        current_cycle_used=trip.current_cycle_used,
        start_time=trip.start_time,
        current_label=trip.current_label,
        pickup_label=trip.pickup_label,
        dropoff_label=trip.dropoff_label,
        current_lat=trip.current_lat,
        current_lon=trip.current_lon,
        pickup_lat=trip.pickup_lat,
        pickup_lon=trip.pickup_lon,
        dropoff_lat=trip.dropoff_lat,
        dropoff_lon=trip.dropoff_lon,
        total_distance_miles=trip.total_distance_miles,
        total_duration_minutes=trip.total_duration_minutes,
        route_geometry=trip.route_geometry,
        legs=trip.legs,
        # Carried over rather than looked up again: the parking options are a
        # property of the route, which has not changed.
        facilities=trip.facilities,
        forced_stops=forced_stops,
        replanned_from=trip,
        distances_estimated=trip.distances_estimated,
        truck_routed=trip.truck_routed,
        no_road_route=trip.no_road_route,
    )

    _store_events(replan, events)
    return replan


def _store_events(trip: Trip, events) -> None:
    DutyEvent.objects.bulk_create(
        DutyEvent(
            trip=trip,
            sequence=index,
            status=event.status.value,
            start_at=event.start,
            end_at=event.end,
            location=event.location,
            note=event.note,
            miles=round(event.miles, 2),
        )
        for index, event in enumerate(events)
    )


def _parkable_miles(events) -> list[float]:
    """Route mile marker of every stop the driver has to park the truck for.

    A stop happens where the driving stopped, so its marker is the miles covered
    so far -- the same quantity the serializer interpolates map positions from,
    derived the same way so the two cannot disagree about where a rest happened.
    """
    markers: list[float] = []
    travelled = 0.0
    for event in events:
        if event.status is DutyStatus.DRIVING:
            travelled += event.miles
        elif stop_kind(event.status.value, event.note) in PARKABLE_KINDS:
            markers.append(travelled)
    return markers


#: A label the client never managed to replace with a place name -- "9.44, 123.32".
_COORDINATE_LABEL = re.compile(
    r"^\s*[-+]?\d{1,3}(?:\.\d+)?\s*,\s*[-+]?\d{1,3}(?:\.\d+)?\s*$"
)


def _resolve(query: str, latitude: float | None, longitude: float | None) -> Place:
    """Turn one input into a ``Place``, looking it up only if we have to.

    A picked or pinned point is already resolved, and its label is whatever the
    driver saw when they chose it -- re-geocoding that text could quietly move
    the trip to a different town of the same name.

    The exception is a label that is still a bare coordinate pair. The map pins
    optimistically and shows the coordinate until Nominatim answers, about a
    second later; submitting inside that window would otherwise store the
    placeholder permanently, and Sec. 395.8 wants a city and state in the
    Remarks column. Naming it here degrades back to the same coordinate when
    the lookup fails, so there is nothing to lose by trying.
    """
    if latitude is not None and longitude is not None:
        if _COORDINATE_LABEL.match(query):
            # Only the name is borrowed. The coordinates stay exactly where the
            # driver put them, rounded solely to share the lookup cache.
            named = reverse(round(latitude, 4), round(longitude, 4))
            return Place(
                query=query,
                label=named.label,
                latitude=latitude,
                longitude=longitude,
            )
        return Place(query=query, label=query, latitude=latitude, longitude=longitude)
    return geocode(query)


def _combined_geometry(*legs: RouteLeg) -> list[list[float]]:
    """Concatenate leg geometries, dropping the seam duplicated at each join."""
    combined: list[list[float]] = []
    for leg in legs:
        points = leg.geometry
        if combined and points and combined[-1] == points[0]:
            points = points[1:]
        combined.extend(points)
    return combined


def geocoded_place(query: str) -> Place:  # pragma: no cover - thin re-export
    return geocode(query)
