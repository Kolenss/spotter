"""Tests for polyline interpolation and simplification."""

from __future__ import annotations

import pytest

from routing.geometry import (
    cumulative_miles,
    haversine_miles,
    point_at_fraction,
    simplify,
)

# A straight run east along the equator: 1 degree of longitude ~= 69.1 miles.
EQUATOR = [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [3.0, 0.0]]


# -- Distance along a polyline ----------------------------------------------


def test_cumulative_miles_accumulates_from_zero():
    running = cumulative_miles(EQUATOR)

    assert running[0] == 0
    assert len(running) == len(EQUATOR)
    assert running[-1] == pytest.approx(207.4, abs=1.0)  # 3 degrees
    assert running == sorted(running)


def test_haversine_matches_a_known_distance():
    # Dallas to Chicago, great-circle, ~800 mi.
    miles = haversine_miles(32.7767, -96.7970, 41.8781, -87.6298)
    assert miles == pytest.approx(803, abs=10)


# -- Interpolating a point partway along --------------------------------------


@pytest.mark.parametrize(
    "fraction,expected_lon",
    [(0.0, 0.0), (0.25, 0.75), (0.5, 1.5), (1.0, 3.0)],
)
def test_point_at_fraction_walks_the_line(fraction, expected_lon):
    point = point_at_fraction(EQUATOR, fraction)
    assert point[0] == pytest.approx(expected_lon, abs=0.01)
    assert point[1] == pytest.approx(0.0, abs=0.01)


def test_fraction_is_by_distance_not_by_vertex_count():
    """Vertices are unevenly spaced, so the midpoint must not be vertex 1."""
    uneven = [[0.0, 0.0], [0.1, 0.0], [10.0, 0.0]]

    midpoint = point_at_fraction(uneven, 0.5)

    assert midpoint[0] == pytest.approx(5.0, abs=0.01)


@pytest.mark.parametrize("fraction", [-1.0, 1.5])
def test_out_of_range_fractions_clamp_to_the_ends(fraction):
    point = point_at_fraction(EQUATOR, fraction)
    assert point in ([0.0, 0.0], [3.0, 0.0])


@pytest.mark.parametrize("geometry", [[], [[1.0, 2.0]]])
def test_degenerate_geometry_does_not_crash(geometry):
    result = point_at_fraction(geometry, 0.5)
    assert result is None or result == [1.0, 2.0]


def test_precomputed_cumulative_gives_the_same_answer():
    running = cumulative_miles(EQUATOR)
    assert point_at_fraction(EQUATOR, 0.4, running) == point_at_fraction(EQUATOR, 0.4)


# -- Simplification ----------------------------------------------------------


def test_simplify_drops_collinear_points():
    # Every interior point lies exactly on the line, so all should go.
    assert simplify(EQUATOR) == [[0.0, 0.0], [3.0, 0.0]]


def test_simplify_always_keeps_both_endpoints():
    detailed = [[i / 100, (i % 7) / 100] for i in range(500)]

    reduced = simplify(detailed)

    assert reduced[0] == detailed[0]
    assert reduced[-1] == detailed[-1]
    assert len(reduced) < len(detailed)


def test_simplify_preserves_a_real_corner():
    corner = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]
    assert simplify(corner) == corner


def test_simplify_respects_tolerance():
    zigzag = [[i * 0.01, (0.02 if i % 2 else 0.0)] for i in range(60)]

    coarse = simplify(zigzag, tolerance=0.5)
    fine = simplify(zigzag, tolerance=0.0001)

    assert len(coarse) < len(fine) <= len(zigzag)


@pytest.mark.parametrize("geometry", [[], [[1.0, 2.0]], [[1.0, 2.0], [3.0, 4.0]]])
def test_simplify_handles_short_input(geometry):
    assert simplify(geometry) == geometry


def test_simplify_does_not_mutate_its_input():
    original = [list(point) for point in EQUATOR]
    simplify(original)
    assert original == EQUATOR


def test_simplify_survives_a_route_large_enough_to_blow_a_recursive_stack():
    """OSRM returns ~14,000 vertices for a cross-country route."""
    huge = [[i / 1000, (i % 13) / 1000] for i in range(14_000)]

    reduced = simplify(huge)

    assert 2 <= len(reduced) < len(huge)
