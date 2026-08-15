"""Real places to stop, found along a planned route.

The HOS engine decides *when* a driver must stop; it has no idea *where* they
can. A 10-hour rest emitted at mile 613 gets interpolated onto the route
polyline at serialization, and that point is just as likely to be the shoulder
of an interstate as anywhere useful. Finding legal overnight parking is the
hardest logistical part of a driver's day -- lots fill by early evening.

This layer answers the other half of the question: given the mile markers of the
stops the regulation forced, which truck stops, rest areas and truck-legal fuel
stations sit at or *before* each one.

**Backwards only, and that is the entire safety argument.** Candidates are drawn
from ``[stop_miles - lookback, stop_miles]`` and never past the marker. Stopping
earlier than the engine planned is always legal -- you simply begin the rest
sooner. Stopping later means driving past a clock, which is a violation. Offering
a facility 10 miles further on would be offering a violation.

Data is OpenStreetMap via Overpass: free, key-less, and good enough in the US
that the major chains are mapped. Like the rest of this package it degrades to
nothing rather than raising -- no facilities simply means the timeline reads
exactly as it did before this module existed.

What this cannot tell you: whether the parking is *full*. No public dataset
carries live space counts. This says where the parking is, not whether it's
available at 9pm.
"""

from __future__ import annotations

import logging
import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Sequence

import requests

from .geometry import Point, cumulative_miles, haversine_miles

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

#: Our own socket timeout. Deliberately longer than the QL timeout below so the
#: server gives up first and answers with an error we can read, rather than us
#: hanging up on work it is still doing.
REQUEST_TIMEOUT_SECONDS = 12

#: Overpass's own budget for the query, in seconds.
OVERPASS_QL_TIMEOUT = 10

#: How far back from a stop to look. Fifty miles is roughly the distance between
#: consecutive services on a US interstate, so it nearly always contains at least
#: one option without pulling in somewhere the driver would resent being sent.
DEFAULT_LOOKBACK_MILES = 50.0

#: How far off the route a facility may sit. A truck stop ten miles down a side
#: road is not a candidate -- the detour costs more clock than the stop saves.
CORRIDOR_WIDTH_MILES = 1.5

#: Overpass charges by area, and one statement per tag per window adds up. Long
#: trips are capped here rather than being allowed to build a query that times
#: out and returns nothing at all.
MAX_WINDOWS = 12

#: Degrees of latitude per mile. Longitude is this over cos(latitude).
MILES_PER_DEGREE_LATITUDE = 69.0

#: OSM tag combinations worth offering a driver, mapped to our own kind.
#: ``nwr`` matches nodes, ways and relations in one statement; services and
#: rest areas are usually mapped as areas rather than points.
_TAG_QUERIES = (
    ('["highway"="services"]', "truck_stop"),
    ('["highway"="rest_area"]', "rest_area"),
    ('["amenity"="fuel"]["hgv"="yes"]', "fuel"),
    ('["amenity"="parking"]["hgv"="yes"]', "parking"),
)

#: Fallback names, used when OSM has the feature but nobody has named it.
_KIND_FALLBACK_NAME = {
    "truck_stop": "Services",
    "rest_area": "Rest area",
    "fuel": "Truck fuel",
    "parking": "Truck parking",
}


@dataclass(frozen=True)
class Facility:
    """One place a driver could legally put the truck.

    ``route_miles`` is expressed in the same road miles the engine works in, so
    it can be compared directly against a stop's mile marker without the caller
    knowing anything about polyline arithmetic.
    """

    osm_id: str
    kind: str
    name: str
    latitude: float
    longitude: float
    route_miles: float
    #: How far off the route it sits, for a driver deciding if it is worth it.
    detour_miles: float
    amenities: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "osm_id": self.osm_id,
            "kind": self.kind,
            "name": self.name,
            "lat": round(self.latitude, 6),
            "lon": round(self.longitude, 6),
            "route_miles": round(self.route_miles, 1),
            "detour_miles": round(self.detour_miles, 2),
            "amenities": list(self.amenities),
        }


def find_along(
    geometry: list[Point],
    stop_miles: Sequence[float],
    total_miles: float,
    *,
    cumulative: list[float] | None = None,
    lookback_miles: float = DEFAULT_LOOKBACK_MILES,
) -> tuple[Facility, ...]:
    """Truck-legal stopping places within reach of each mile marker.

    ``stop_miles`` are road miles from the start of the trip, as the engine
    counts them. ``total_miles`` is the route's total road distance, needed
    because the polyline's great-circle length and the routing service's
    reported road distance differ by a percent or two -- the conversion runs
    through fractions for the same reason ``point_at_fraction`` does.

    Returns an empty tuple for any failure, including no network at all.
    """
    if len(geometry) < 2 or not stop_miles or total_miles <= 0:
        return ()

    running = cumulative if cumulative is not None else cumulative_miles(geometry)
    polyline_miles = running[-1]
    if polyline_miles <= 0:
        return ()

    # Road miles -> polyline miles, so index lookups against `running` are valid.
    scale = polyline_miles / total_miles
    windows = _merge_windows(
        (max(0.0, (miles - lookback_miles) * scale), miles * scale)
        for miles in sorted(stop_miles)
    )

    if len(windows) > MAX_WINDOWS:
        logger.info(
            "Route has %d stop corridors; querying the first %d only.",
            len(windows),
            MAX_WINDOWS,
        )
        windows = windows[:MAX_WINDOWS]

    spans = [_index_span(running, start, end) for start, end in windows]
    spans = [span for span in spans if span is not None]
    if not spans:
        return ()

    boxes = tuple(_bounding_box(geometry, low, high) for low, high in spans)

    try:
        elements = _fetch(_build_query(boxes))
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Overpass lookup failed (%s); continuing without facilities.", exc)
        return ()

    found: dict[str, Facility] = {}
    for element in elements:
        parsed = _parse_element(element)
        if parsed is None:
            continue
        osm_id, kind, name, latitude, longitude, amenities = parsed

        nearest = _nearest_on_route(geometry, running, spans, latitude, longitude)
        if nearest is None:
            continue
        detour, at_polyline_miles = nearest
        if detour > CORRIDOR_WIDTH_MILES:
            continue

        # Back to road miles so the caller can compare with the engine directly.
        found[osm_id] = Facility(
            osm_id=osm_id,
            kind=kind,
            name=name,
            latitude=latitude,
            longitude=longitude,
            route_miles=at_polyline_miles / scale,
            detour_miles=detour,
            amenities=amenities,
        )

    return tuple(sorted(found.values(), key=lambda facility: facility.route_miles))


# -- Query building ----------------------------------------------------------


def _merge_windows(windows: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    """Collapse overlapping corridors into one.

    Consecutive stops are often closer together than the lookback distance, so
    without this a trip with six stops would ask Overpass for six overlapping
    boxes covering much the same ground.
    """
    merged: list[tuple[float, float]] = []
    for start, end in windows:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _index_span(
    running: list[float], start_miles: float, end_miles: float
) -> tuple[int, int] | None:
    """Vertex index range covering a stretch of the polyline.

    Bounding the vertex scan this way is what keeps the nearest-point search
    affordable: full-resolution geometry runs to ~14,000 vertices, and a 50-mile
    corridor of a cross-country route is a few hundred of them.
    """
    low = max(0, bisect_left(running, start_miles) - 1)
    high = min(len(running) - 1, bisect_right(running, end_miles))
    return (low, high) if high > low else None


def _bounding_box(
    geometry: list[Point], low: int, high: int
) -> tuple[float, float, float, float]:
    """South, west, north, east around a stretch of route, padded by the corridor."""
    lats = [point[1] for point in geometry[low : high + 1]]
    lons = [point[0] for point in geometry[low : high + 1]]

    pad_lat = CORRIDOR_WIDTH_MILES / MILES_PER_DEGREE_LATITUDE
    mid_lat = (min(lats) + max(lats)) / 2
    # Degrees of longitude shrink towards the poles; at 45 deg a mile is ~1.4x
    # the degrees it is at the equator, and ignoring that would clip candidates
    # off the eastern and western edges of a north-south corridor.
    pad_lon = pad_lat / max(0.1, math.cos(math.radians(mid_lat)))

    return (
        round(min(lats) - pad_lat, 3),
        round(min(lons) - pad_lon, 3),
        round(max(lats) + pad_lat, 3),
        round(max(lons) + pad_lon, 3),
    )


def _build_query(boxes: tuple[tuple[float, float, float, float], ...]) -> str:
    """One Overpass query covering every corridor.

    A single request rather than one per stop: Overpass permits only a couple of
    concurrent queries per client and is the slowest thing in the plan, so eight
    round trips would dominate the response time and risk being throttled.
    """
    statements = [
        f"nwr{tags}({south},{west},{north},{east});"
        for south, west, north, east in boxes
        for tags, _ in _TAG_QUERIES
    ]
    body = "\n  ".join(statements)
    # `out center` gives ways and relations a centroid; without it an area comes
    # back with member references and no coordinate of its own.
    return f"[out:json][timeout:{OVERPASS_QL_TIMEOUT}];\n(\n  {body}\n);\nout center tags;"


@lru_cache(maxsize=64)
def _fetch(query: str) -> tuple[dict, ...]:
    """POST the query and return its elements.

    Cached on the query text, which is deterministic for a given route: opening
    the same trip twice costs one lookup. Failures raise rather than returning
    empty so that ``lru_cache`` does not memoise an outage -- it only caches
    successful returns.
    """
    response = requests.post(
        OVERPASS_URL,
        data={"data": query},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    elements = payload.get("elements") if isinstance(payload, dict) else None
    return tuple(elements or ())


# -- Parsing -----------------------------------------------------------------


def _parse_element(element: dict) -> tuple[str, str, str, float, float, tuple[str, ...]] | None:
    tags = element.get("tags") or {}
    kind = _kind_for(tags)
    if kind is None:
        return None

    # Nodes carry lat/lon directly; ways and relations get a `center` from
    # `out center`.
    centre = element.get("center") or {}
    latitude = element.get("lat", centre.get("lat"))
    longitude = element.get("lon", centre.get("lon"))
    if latitude is None or longitude is None:
        return None

    osm_id = f"{element.get('type', 'node')}/{element.get('id')}"
    name = tags.get("name") or _KIND_FALLBACK_NAME.get(kind, "Stopping place")

    return osm_id, kind, name, float(latitude), float(longitude), _amenities(tags)


def _kind_for(tags: dict) -> str | None:
    if tags.get("highway") == "services":
        return "truck_stop"
    if tags.get("highway") == "rest_area":
        return "rest_area"
    if tags.get("amenity") == "fuel" and tags.get("hgv") == "yes":
        return "fuel"
    if tags.get("amenity") == "parking" and tags.get("hgv") == "yes":
        return "parking"
    return None


#: OSM spells "yes" several ways for these.
_TRUTHY = {"yes", "true", "1", "designated"}


def _amenities(tags: dict) -> tuple[str, ...]:
    """The handful of facts that decide whether a driver stops here.

    Deliberately short. A driver scanning a list at the end of a shift wants to
    know about a shower and a toilet, not the surface material of the car park.
    """
    found = []
    if tags.get("shower") in _TRUTHY:
        found.append("shower")
    if tags.get("toilets") in _TRUTHY or tags.get("amenity") == "toilets":
        found.append("toilets")
    if tags.get("fuel:diesel") in _TRUTHY or tags.get("fuel:hgv_diesel") in _TRUTHY:
        found.append("diesel")
    if tags.get("internet_access") in _TRUTHY or tags.get("internet_access") == "wlan":
        found.append("wifi")
    return tuple(found)


# -- Geometry ----------------------------------------------------------------


def _nearest_on_route(
    geometry: list[Point],
    running: list[float],
    spans: list[tuple[int, int]],
    latitude: float,
    longitude: float,
) -> tuple[float, float] | None:
    """Closest vertex within the searched corridors, as (miles off, miles along).

    Vertex-level precision is ample: at full resolution OSRM emits a point every
    few metres, so the error is far below the 1.5-mile corridor this feeds.
    """
    best: tuple[float, float] | None = None
    for low, high in spans:
        for index in range(low, high + 1):
            point = geometry[index]
            distance = haversine_miles(latitude, longitude, point[1], point[0])
            if best is None or distance < best[0]:
                best = (distance, running[index])
    return best
