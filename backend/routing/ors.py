"""Truck-legal routing through OpenRouteService.

OSRM's public server runs a car profile and nothing else. It will happily send
a 13'6" trailer under a 12'4" bridge, down a parkway that bans commercial
vehicles, or over a posted-weight bridge -- and the mileage it returns for that
illegal route then feeds every clock the HOS engine computes. Routing a truck
on a car profile is wrong twice over.

ORS exposes a ``driving-hgv`` profile that takes the vehicle's dimensions and
weight and routes around the restrictions OSM knows about. That qualifier
matters and is worth stating plainly: ORS routes on OpenStreetMap, so it can
only avoid a low bridge that somebody has tagged with ``maxheight``. US
coverage of those tags is patchy. This is a large improvement on a car profile
and it is not a substitute for a commercial truck-attribute dataset.

Requires a free API key -- name, email and a captcha, no payment details -- from
account.heigit.org. Without one configured this module is never called and
``routing.client`` stays on OSRM, so the app works exactly as before.
"""

from __future__ import annotations

import logging
import os

import requests

from .trucks import TruckSpec

logger = logging.getLogger(__name__)

#: Environment variable holding the free ORS key. Absent means "stay on OSRM".
API_KEY_ENV = "ORS_API_KEY"


def configured_key() -> str:
    """The ORS key, or an empty string when truck routing is switched off.

    Read on every call rather than captured at import time, so pasting a key
    into ``.env`` takes effect on the next request under ``runserver``'s
    autoreload instead of needing a manual restart to be noticed.
    """
    return os.environ.get(API_KEY_ENV, "").strip()

ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-hgv/geojson"
ORS_SNAP_URL = "https://api.openrouteservice.org/v2/snap/driving-hgv/json"

REQUEST_TIMEOUT_SECONDS = 15

METRES_PER_MILE = 1609.344

#: How far to look for a road a truck may legally use.
#:
#: Directions refuse anything past 350 m and will not be argued with -- their
#: own ``radiuses`` parameter answers "the maximum possible radius of 350.0
#: meters" even when asked for unlimited. Snapping has no such cap, so the
#: point is moved onto the truck network *before* directions ever sees it.
#: Beyond five kilometres the pin is not really near a road a lorry can use,
#: and moving it further would be inventing a destination.
SNAP_RADIUS_METRES = 5000


class TruckRoutingUnavailable(RuntimeError):
    """ORS could not answer, so the caller should fall back to OSRM."""


def snap_to_truck_road(
    latitude: float, longitude: float, api_key: str
) -> tuple[float, float, float] | None:
    """Nearest point a lorry may legally reach, or None if there isn't one.

    Snapping against the *same* profile that will do the routing is the whole
    point. A car-network snap is not good enough: OSRM will happily attach a
    pin to a forest track or a weight-restricted lane, and directions then
    refuses the very point snapping just produced -- observed on a South
    Carolina pin that OSRM moved 849 m onto a road ORS would not route from.

    None means no truck-legal road within ``SNAP_RADIUS_METRES``, which is a
    real answer about the place, not a failure.
    """
    try:
        response = requests.post(
            ORS_SNAP_URL,
            json={"locations": [[longitude, latitude]], "radius": SNAP_RADIUS_METRES},
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        # A location that could not be snapped comes back as a null entry
        # rather than being omitted, so the list still lines up with the input.
        snapped = (payload.get("locations") or [None])[0]
    except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
        logger.info("Truck-road snapping unavailable (%s)", exc)
        return None

    if not snapped:
        return None

    longitude_out, latitude_out = snapped["location"]
    return (
        float(latitude_out),
        float(longitude_out),
        float(snapped.get("snapped_distance", 0.0)),
    )


def truck_route(
    origin_lon: float,
    origin_lat: float,
    dest_lon: float,
    dest_lat: float,
    spec: TruckSpec,
    api_key: str,
) -> tuple[float, int, list[list[float]]]:
    """Miles, minutes and geometry for a truck-legal route.

    Returns geometry as GeoJSON ``[lon, lat]`` pairs -- ORS speaks GeoJSON
    natively, which is already the shape ``Trip.route_geometry`` stores, so
    nothing has to be transposed or decoded on the way through.

    Raises ``TruckRoutingUnavailable`` for anything that goes wrong, including a
    route ORS cannot find. The caller treats that as "use the car route", which
    is worse but honest, rather than failing the trip.
    """
    body = {
        "coordinates": [[origin_lon, origin_lat], [dest_lon, dest_lat]],
        # Without this ORS still applies the hgv profile's speeds but not the
        # dimensional restrictions, which is the entire point of being here.
        "options": {"profile_params": {"restrictions": spec.as_ors_restrictions()}},
    }

    try:
        response = requests.post(
            ORS_DIRECTIONS_URL,
            json=body,
            headers={
                # ORS takes the key as a bare Authorization value -- no
                # "Bearer" prefix, which its own examples omit too.
                "Authorization": api_key,
                "Content-Type": "application/json",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        feature = payload["features"][0]
        summary = feature["properties"]["summary"]
        coordinates = feature["geometry"]["coordinates"]
    except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
        raise TruckRoutingUnavailable(str(exc)) from exc

    # A summary with no distance is ORS's shape for "the profile could not get
    # there" -- a truck-illegal destination, typically. Not an error, but not a
    # route either.
    metres = summary.get("distance", 0)
    seconds = summary.get("duration", 0)
    if metres <= 0 or seconds <= 0 or len(coordinates) < 2:
        raise TruckRoutingUnavailable("ORS returned an empty route")

    return (
        round(metres / METRES_PER_MILE, 1),
        max(1, round(seconds / 60)),
        [[float(point[0]), float(point[1])] for point in coordinates],
    )
