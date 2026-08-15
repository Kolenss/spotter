"""Ties the routing layer and the HOS engine together and persists the result."""

from __future__ import annotations

import re
from datetime import datetime

from django.db import transaction

from hos.planner import Leg, plan_trip
from routing.client import GeocodingError, Place, RouteLeg, geocode, reverse, route

from .models import DutyEvent, Trip

__all__ = ["GeocodingError", "build_trip"]


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
        total_distance_miles=round(to_pickup.miles + to_dropoff.miles, 1),
        total_duration_minutes=to_pickup.duration_minutes + to_dropoff.duration_minutes,
        route_geometry=_combined_geometry(to_pickup, to_dropoff),
        distances_estimated=to_pickup.is_estimate or to_dropoff.is_estimate,
        no_road_route=to_pickup.is_unroutable or to_dropoff.is_unroutable,
    )

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

    return trip


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
