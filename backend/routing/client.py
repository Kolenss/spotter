"""Geocoding and routing against free, key-less OpenStreetMap services.

Phase 1 has no map UI, but the HOS engine still needs real distances and drive
times to produce a meaningful schedule, so this layer runs headless. Route
geometry is fetched and returned anyway -- it is persisted unused so that
adding the map later is a frontend-only change.

Both providers are public community servers with no SLA and aggressive rate
limits, so every call degrades to a great-circle estimate rather than failing.
A demo that shows slightly approximate mileage beats a demo that 500s.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from functools import lru_cache

import requests

from hos.rules import DEFAULT_SPEED_MPH

from .ors import TruckRoutingUnavailable, configured_key, truck_route
from .trucks import STANDARD_DRY_VAN

logger = logging.getLogger(__name__)

NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
OSRM_URL = "https://router.project-osrm.org/route/v1/driving"

#: Zoom level for reverse lookups. Nominatim's default of 18 resolves to a
#: building; Sec. 395.8 wants the town, and a driver dropping a pin on an
#: interstate wants the nearest place name rather than a field boundary.
REVERSE_ZOOM = 10

#: Nominatim's usage policy requires a descriptive User-Agent identifying the
#: application; requests without one are rejected.
USER_AGENT = "spotter-eld-trip-planner/1.0 (HOS assessment project)"

REQUEST_TIMEOUT_SECONDS = 12

#: Great-circle distance under-reports road distance. This multiplier is the
#: usual planning approximation for US highway routing, used only in fallback.
ROAD_WINDING_FACTOR = 1.20

EARTH_RADIUS_MILES = 3958.8


class GeocodingError(ValueError):
    """Raised when a user-supplied location cannot be resolved at all."""


@dataclass
class Place:
    query: str
    label: str
    latitude: float
    longitude: float


@dataclass
class RouteLeg:
    miles: float
    duration_minutes: int
    #: GeoJSON [lon, lat] pairs. Stored now, drawn when the map ships.
    geometry: list[list[float]] = field(default_factory=list)
    #: "ors" for a truck-legal route; "osrm" for a car road route; "estimate"
    #: when the service failed us; "no-route" when it answered correctly that
    #: no drivable path exists.
    source: str = "osrm"

    @property
    def is_estimate(self) -> bool:
        """True for either fallback -- the mileage is approximate both ways."""
        return self.source in ("estimate", "no-route")

    @property
    def is_truck_legal(self) -> bool:
        """True when a truck profile produced this, restrictions and all.

        False means the mileage came from a car profile or a straight line, and
        the route may cross something an 80,000 lb combination cannot legally
        or physically use. Surfaced to the driver rather than hidden.
        """
        return self.source == "ors"

    @property
    def is_unroutable(self) -> bool:
        """No road connects these points, so retrying will never help.

        Distinct from ``is_estimate``: an outage is transient and worth
        retrying, a disconnected road graph is permanent. The Darien Gap and
        roadless Alaska are the two that come up in practice.
        """
        return self.source == "no-route"


@lru_cache(maxsize=256)
def search(query: str, limit: int = 5) -> tuple[Place, ...]:
    """Candidate matches for a free-text location, best first.

    Returns a tuple rather than a list because the result is cached and shared
    between callers; a mutable list would let one caller corrupt another's.

    An empty tuple means "no such place", which is a normal answer the caller
    can show as an empty dropdown. A service outage raises instead, because
    that is not the same thing and the driver should be told so.
    """
    cleaned = query.strip()
    if not cleaned:
        raise GeocodingError("Location cannot be empty.")

    try:
        response = requests.get(
            NOMINATIM_SEARCH_URL,
            params={
                "q": cleaned,
                "format": "json",
                "limit": limit,
                "addressdetails": 0,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        results = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise GeocodingError(
            f"Could not look up “{cleaned}” — the geocoding service is "
            f"unavailable. Please try again."
        ) from exc

    return tuple(
        Place(
            query=cleaned,
            label=_short_label(result.get("display_name", cleaned)),
            latitude=float(result["lat"]),
            longitude=float(result["lon"]),
        )
        for result in results
    )


def geocode(query: str) -> Place:
    """Resolve a free-text location to a single best-match coordinate.

    A thin wrapper over ``search`` so the request and error handling live in
    one place. Caching happens there.
    """
    matches = search(query, limit=1)
    if not matches:
        raise GeocodingError(
            f"Could not find a location matching “{query.strip()}”. "
            f"Try adding a state or country, e.g. “Springfield, IL”."
        )
    return matches[0]


@lru_cache(maxsize=256)
def reverse(latitude: float, longitude: float) -> Place:
    """Name the place at a coordinate, for a pin dropped straight onto the map.

    Unlike ``geocode`` this degrades instead of raising. The driver has already
    given us a usable position by pinning it; failing to find its name is a
    cosmetic loss, and refusing to plan the trip over it would be absurd. The
    fallback label is the coordinate itself, which is at least truthful.

    Callers should round before calling: a dragged marker emits a coordinate
    per pixel, and each distinct value is a cache miss and a fresh request.
    """
    fallback = f"{latitude:.2f}, {longitude:.2f}"
    try:
        response = requests.get(
            NOMINATIM_REVERSE_URL,
            params={
                "lat": latitude,
                "lon": longitude,
                "format": "json",
                "zoom": REVERSE_ZOOM,
                "addressdetails": 0,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        logger.warning("Reverse geocoding unavailable for %s", fallback)
        return Place(fallback, fallback, latitude, longitude)

    # Nominatim answers open ocean and other unnamed places with {"error": …}.
    display_name = payload.get("display_name") if isinstance(payload, dict) else None
    if not display_name:
        return Place(fallback, fallback, latitude, longitude)

    return Place(
        query=fallback,
        label=_short_label(display_name),
        latitude=latitude,
        longitude=longitude,
    )


def route(origin: Place, destination: Place) -> RouteLeg:
    """Best available route between two places.

    Three tiers, each a strictly worse but still usable answer than the last:

    1. **OpenRouteService ``driving-hgv``** -- a truck-legal route that honours
       height, weight, length and axle load. Only when a key is configured.
    2. **OSRM** -- a car route. Right roads, wrong vehicle: it does not know
       about low bridges or lorry bans, so the mileage is plausible and the
       route may not be drivable by a truck.
    3. **Haversine** -- a straight line with a winding factor.

    A demo that shows approximate mileage beats a demo that 500s, and that logic
    extends upwards: a car route beats no route, and is flagged as such rather
    than passed off as truck-legal.
    """
    truck = _truck_route(origin, destination)
    if truck is not None:
        return truck

    coords = (
        f"{origin.longitude},{origin.latitude};"
        f"{destination.longitude},{destination.latitude}"
    )
    try:
        response = requests.get(
            f"{OSRM_URL}/{coords}",
            params={"overview": "full", "geometries": "geojson"},
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        # OSRM reports "there is no such route" as a 400 carrying a code, which
        # is a real answer rather than a failure. Read the body before
        # raise_for_status turns it into an indistinguishable exception.
        if _is_unroutable(response):
            logger.info(
                "No drivable route between %s and %s; using distance estimate.",
                origin.label,
                destination.label,
            )
            return _estimated_route(origin, destination, source="no-route")

        response.raise_for_status()
        payload = response.json()
        leg = payload["routes"][0]
    except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
        logger.warning(
            "OSRM routing failed for %s -> %s (%s); using distance estimate.",
            origin.label,
            destination.label,
            exc,
        )
        return _estimated_route(origin, destination)

    miles = leg["distance"] / 1609.344
    minutes = round(leg["duration"] / 60)
    geometry = leg.get("geometry", {}).get("coordinates", [])

    if miles <= 0 or minutes <= 0:
        return _estimated_route(origin, destination)

    return RouteLeg(
        miles=round(miles, 1),
        duration_minutes=minutes,
        geometry=geometry,
        source="osrm",
    )


def _truck_route(origin: Place, destination: Place) -> RouteLeg | None:
    """A truck-legal route, or ``None`` to mean "fall through to OSRM".

    The key comes from the environment rather than Django settings on purpose:
    every other module in ``routing/`` is free of Django imports, which is what
    lets this layer be exercised as a plain library. ``load_dotenv()`` in
    settings.py has already put ``.env`` into ``os.environ`` by the time any
    request arrives, so reading it here sees the same value.
    """
    api_key = configured_key()
    if not api_key:
        return None

    try:
        miles, minutes, geometry = truck_route(
            origin.longitude,
            origin.latitude,
            destination.longitude,
            destination.latitude,
            STANDARD_DRY_VAN,
            api_key,
        )
    except TruckRoutingUnavailable as exc:
        logger.warning(
            "Truck routing unavailable for %s -> %s (%s); falling back to the "
            "car profile, which does not know about bridge heights.",
            origin.label,
            destination.label,
            exc,
        )
        return None

    return RouteLeg(
        miles=miles, duration_minutes=minutes, geometry=geometry, source="ors"
    )


def _is_unroutable(response) -> bool:
    """True when OSRM says no road path exists, rather than failing to answer.

    ``NoRoute`` means the endpoints sit in disconnected components of the road
    graph; ``NoSegment`` means one of them has no road near it at all. Both are
    permanent for the given coordinates, so the UI should not invite a retry.
    """
    if response.status_code != 400:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("code") in ("NoRoute", "NoSegment")


def _estimated_route(
    origin: Place, destination: Place, source: str = "estimate"
) -> RouteLeg:
    straight = _haversine_miles(
        origin.latitude, origin.longitude, destination.latitude, destination.longitude
    )
    miles = straight * ROAD_WINDING_FACTOR
    return RouteLeg(
        miles=round(miles, 1),
        duration_minutes=max(1, round(miles / DEFAULT_SPEED_MPH * 60)),
        geometry=[
            [origin.longitude, origin.latitude],
            [destination.longitude, destination.latitude],
        ],
        source=source,
    )


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return EARTH_RADIUS_MILES * 2 * math.asin(math.sqrt(a))


def _short_label(display_name: str) -> str:
    """Trim Nominatim's verbose display name to something a log sheet can hold.

    Sec. 395.8 wants city and state in the Remarks column, not a full postal
    address, and the grid has very little horizontal room.
    """
    parts = [part.strip() for part in display_name.split(",")]
    if len(parts) <= 2:
        return display_name
    city = parts[0]
    # Drop postcode and country, keep the administrative region before them.
    tail = [p for p in parts[1:-1] if not p.replace(" ", "").isdigit()]
    region = tail[-1] if tail else parts[-1]
    return f"{city}, {region}"
