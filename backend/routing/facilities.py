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
import time
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Sequence

import requests

from .client import USER_AGENT
from .geometry import Point, cumulative_miles, haversine_miles

logger = logging.getLogger(__name__)

#: Overpass endpoints, tried in order until one answers.
#:
#: The main endpoint refuses connections outright when saturated -- it was
#: unreachable from the development machine throughout this build -- so a
#: fallback is worth having. A driver should not lose their parking options
#: because one volunteer-run server is having a bad afternoon.
#:
#: **Only planet-wide instances belong here.** ``overpass.osm.ch`` was tried and
#: removed: it is the Swiss chapter's server and carries Switzerland alone, so
#: it answers a query over Nebraska with ``200 OK`` and zero elements. That is
#: strictly worse than an error, because it looks exactly like "there is nowhere
#: to park here" and stops the failover chain before a real server is asked.
#:
#: Ordered by what answers *this* query, measured rather than assumed. The
#: distinction matters: a trivial one-tag probe is answered by mirrors that
#: cannot manage the real thing, so the order below comes from timing the
#: actual corridor query this module builds.
#:
#:     private.coffee   HTTP 200 in 24.2s, 28 elements
#:     kumi.systems     HTTP 504 after 39.2s -- accepts, then gives up
#:     overpass-api.de  ConnectTimeout after 42.1s
#:
#: ``overpass-api.de`` is deliberately absent. It is the canonical instance and
#: the obvious first choice, but while saturated it does not refuse quickly --
#: it takes forty seconds to fail, spending the whole budget before a working
#: mirror is even tried. Put it back at the *front* when it recovers; never in
#: the middle, where its failure delays the servers behind it.
OVERPASS_URLS = (
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

#: Our own socket timeout. Deliberately longer than the QL timeout below so the
#: server gives up first and answers with an error we can read, rather than us
#: hanging up on work it is still doing.
#:
#: Sized from what the mirrors actually take, not from what feels tolerable.
#: This was 5s for a while, which is shorter than any mirror's response time --
#: so every lookup failed, and the failure looked exactly like an outage. A
#: timeout below the service's real latency does not make the feature fast, it
#: makes it impossible. The working mirror answers this query in about 24s, so
#: anything under that returns nothing, every time.
REQUEST_TIMEOUT_SECONDS = 35

#: Overpass's own budget for the query, in seconds. Under the socket timeout
#: above so the server, not us, decides when a query has run too long.
OVERPASS_QL_TIMEOUT = 30

#: How long to stop asking after every mirror has failed.
#:
#: Without this each plan re-pays the full timeout while Overpass is down, and
#: outages last hours, not seconds. `lru_cache` cannot help: it deliberately
#: memoises only successes, so a failure is retried in full every time. Short
#: enough that a recovered service is picked up within a minute.
FAILURE_BACKOFF_SECONDS = 60

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

#: How OSM says "trucks are welcome here". Both spellings have to be accepted:
#: measured over a box of I-80 in Nebraska, seven truck fuel stations carried
#: ``hgv=designated`` -- Flying J among them -- and *not one* carried
#: ``hgv=yes``. Matching only ``yes``, as this first did, found none of them.
#: ``designated`` is the stronger claim of the two: specifically intended for
#: heavy goods vehicles, rather than merely permitted.
_HGV_ALLOWED = frozenset({"yes", "designated"})
_HGV_FILTER = '["hgv"~"^(yes|designated)$"]'

#: OSM tag combinations worth offering a driver, mapped to our own kind.
#: ``nwr`` matches nodes, ways and relations in one statement; services and
#: rest areas are usually mapped as areas rather than points.
#:
#: ``highway=services`` needs no hgv filter -- a motorway service area takes
#: trucks by definition, and it is how the big US travel centres (TA, Flying J)
#: are mapped. Fuel and parking do need it: an ordinary filling station
#: forecourt cannot physically take an 80,000 lb combination.
_TAG_QUERIES = (
    ('["highway"="services"]', "truck_stop"),
    ('["highway"="rest_area"]', "rest_area"),
    (f'["amenity"="fuel"]{_HGV_FILTER}', "fuel"),
    (f'["amenity"="parking"]{_HGV_FILTER}', "parking"),
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


#: When every mirror last failed, as a monotonic timestamp. Module state rather
#: than a cache entry because it is about the *service*, not about one query.
_blocked_until: float = 0.0


class OverpassDown(requests.RequestException):
    """Every mirror failed recently, so we did not ask again."""


def overpass_available() -> bool:
    return time.monotonic() >= _blocked_until


@lru_cache(maxsize=64)
def _fetch(query: str) -> tuple[dict, ...]:
    """POST the query to the first mirror that answers, and return its elements.

    Cached on the query text, which is deterministic for a given route: opening
    the same trip twice costs one lookup. Failures raise rather than returning
    empty so that ``lru_cache`` does not memoise an outage -- it only caches
    successful returns.

    A mirror that is merely *busy* still counts as an answer for our purposes
    only if it returns parseable JSON; several return an HTML error page with a
    200, which is why the body is parsed here rather than by the caller.

    Once every mirror has failed the next minute of calls return immediately
    instead of queueing behind the same timeouts. An outage is a property of the
    service, so the second driver to plan a trip during one should not pay to
    rediscover it.
    """
    global _blocked_until

    if not overpass_available():
        raise OverpassDown("Overpass was unreachable moments ago; not retrying yet")

    last_error: Exception | None = None

    for url in OVERPASS_URLS:
        try:
            # The same User-Agent Nominatim demands, and for the same reason:
            # kumi answers an anonymous request with "429 Please include a
            # meaningful User-Agent string with your requests to avoid
            # rate-limiting". Verified against the live server.
            response = requests.post(
                url,
                data={"data": query},
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.info("Overpass mirror %s unavailable (%s); trying the next.", url, exc)
            last_error = exc
            continue

        # A mirror answered, so the service is not down whatever it said before.
        _blocked_until = 0.0
        elements = payload.get("elements") if isinstance(payload, dict) else None
        return tuple(elements or ())

    _blocked_until = time.monotonic() + FAILURE_BACKOFF_SECONDS
    raise last_error or requests.RequestException("no Overpass mirror answered")


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
    if tags.get("amenity") == "fuel" and tags.get("hgv") in _HGV_ALLOWED:
        return "fuel"
    if tags.get("amenity") == "parking" and tags.get("hgv") in _HGV_ALLOWED:
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
