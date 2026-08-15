"""Locating a point partway along a route polyline.

Stops that happen mid-leg -- a fuel stop 1,000 miles in, a rest when the 11th
driving hour runs out -- have no address of their own. What we do know is how
far along the route they occurred, so their position is found by walking the
route geometry until that much distance has been covered.
"""

from __future__ import annotations

import math

EARTH_RADIUS_MILES = 3958.8

#: A GeoJSON coordinate pair, [longitude, latitude].
Point = list[float]


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return EARTH_RADIUS_MILES * 2 * math.asin(math.sqrt(a))


def cumulative_miles(geometry: list[Point]) -> list[float]:
    """Distance from the start of the polyline to each of its vertices."""
    running = [0.0]
    for (lon1, lat1), (lon2, lat2) in zip(geometry, geometry[1:]):
        running.append(running[-1] + haversine_miles(lat1, lon1, lat2, lon2))
    return running


def simplify(geometry: list[Point], tolerance: float = 0.001) -> list[Point]:
    """Ramer-Douglas-Peucker reduction, for drawing only.

    OSRM returns a vertex every few metres -- around 14,000 points for a
    cross-country route, which is ~300 KB of JSON and far more detail than any
    zoom level can show. This drops vertices that lie within ``tolerance``
    degrees of the line they sit on, preserving the visible shape of every
    curve while shedding most of the weight.

    The *stored* geometry stays at full resolution: stop positions are
    interpolated from it, and simplifying first would move them.
    """
    if len(geometry) < 3:
        return [list(point) for point in geometry]

    keep = [False] * len(geometry)
    keep[0] = keep[-1] = True

    # Iterative rather than recursive -- 14,000 points would blow the stack.
    stack = [(0, len(geometry) - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue

        furthest, furthest_distance = start, -1.0
        for index in range(start + 1, end):
            distance = _perpendicular_distance(
                geometry[index], geometry[start], geometry[end]
            )
            if distance > furthest_distance:
                furthest, furthest_distance = index, distance

        if furthest_distance > tolerance:
            keep[furthest] = True
            stack.append((start, furthest))
            stack.append((furthest, end))

    return [list(point) for point, keeping in zip(geometry, keep) if keeping]


def _perpendicular_distance(point: Point, start: Point, end: Point) -> float:
    """Distance from ``point`` to the segment start-end, in degrees.

    Degrees rather than miles is fine here: this only decides which vertices
    are visually redundant, and the distortion across one route is negligible.
    """
    x, y = point[0], point[1]
    x1, y1 = start[0], start[1]
    x2, y2 = end[0], end[1]

    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(x - x1, y - y1)

    # Project the point onto the segment, clamped to its ends.
    t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))


def point_at_fraction(
    geometry: list[Point], fraction: float, cumulative: list[float] | None = None
) -> Point | None:
    """The coordinate ``fraction`` of the way along the polyline, by distance.

    Works in fractions rather than absolute miles because the polyline's
    great-circle length and the routing service's reported road distance differ
    by a percent or two; a fraction stays correct under either measure.

    ``cumulative`` may be passed in to avoid recomputing it for every stop on
    the same route.
    """
    if not geometry:
        return None
    if len(geometry) == 1:
        return list(geometry[0])

    running = cumulative if cumulative is not None else cumulative_miles(geometry)
    total = running[-1]
    if total <= 0:
        return list(geometry[0])

    target = max(0.0, min(1.0, fraction)) * total

    # Find the segment that straddles the target distance.
    low, high = 0, len(running) - 1
    while low < high:
        mid = (low + high) // 2
        if running[mid] < target:
            low = mid + 1
        else:
            high = mid
    if low == 0:
        return list(geometry[0])

    start_point, end_point = geometry[low - 1], geometry[low]
    span = running[low] - running[low - 1]
    within = 0.0 if span <= 0 else (target - running[low - 1]) / span

    return [
        start_point[0] + (end_point[0] - start_point[0]) * within,
        start_point[1] + (end_point[1] - start_point[1]) * within,
    ]
