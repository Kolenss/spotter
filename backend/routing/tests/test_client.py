"""Routing tests.

These never touch the network -- the public OSRM and Nominatim servers are
rate-limited and would make the suite flaky. The fallback path is what matters
most here anyway: it is what keeps the hosted demo working when those free
services throttle us.
"""

from __future__ import annotations

import pytest
import requests

from routing import client
from routing.client import GeocodingError, Place, geocode, reverse, route, search

DALLAS = Place("Dallas, TX", "Dallas, Texas", 32.7767, -96.7970)
CHICAGO = Place("Chicago, IL", "Chicago, Illinois", 41.8781, -87.6298)


@pytest.fixture(autouse=True)
def clear_lookup_caches():
    """``geocode`` is a thin wrapper; the cache it shares lives on ``search``."""
    for cached in (search, reverse):
        cached.cache_clear()
    yield
    for cached in (search, reverse):
        cached.cache_clear()


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


# -- Fallback behaviour ------------------------------------------------------


def test_route_falls_back_to_an_estimate_when_osrm_is_down(monkeypatch):
    def explode(*args, **kwargs):
        raise requests.ConnectionError("osrm unreachable")

    monkeypatch.setattr(client.requests, "get", explode)

    leg = route(DALLAS, CHICAGO)

    assert leg.is_estimate
    # Straight line is ~803 mi; with the winding factor we expect ~960.
    assert 850 < leg.miles < 1050
    assert leg.duration_minutes > 0
    assert len(leg.geometry) == 2  # origin and destination only


def test_an_outage_is_not_reported_as_an_impossible_route(monkeypatch):
    """The two fallbacks must stay distinguishable -- one is worth retrying."""
    def explode(*args, **kwargs):
        raise requests.ConnectionError("osrm unreachable")

    monkeypatch.setattr(client.requests, "get", explode)

    assert not route(DALLAS, CHICAGO).is_unroutable


@pytest.mark.parametrize("code", ["NoRoute", "NoSegment"])
def test_route_flags_a_disconnected_road_network(monkeypatch, code):
    """OSRM answers "no such route" with a 400 body, not a transport failure.

    Real case: Argentina to Alaska. The Darien Gap leaves South America a
    disconnected component of the road graph, so no retry can ever succeed.
    """
    monkeypatch.setattr(
        client.requests,
        "get",
        lambda *a, **k: FakeResponse({"code": code, "message": "no"}, status=400),
    )

    leg = route(DALLAS, CHICAGO)

    assert leg.is_unroutable
    assert leg.is_estimate  # mileage is still approximate, and still usable
    assert leg.miles > 0
    assert len(leg.geometry) == 2


def test_other_400s_are_treated_as_a_service_failure(monkeypatch):
    """An InvalidQuery is our bug or a hiccup -- not proof that no road exists."""
    monkeypatch.setattr(
        client.requests,
        "get",
        lambda *a, **k: FakeResponse({"code": "InvalidQuery"}, status=400),
    )

    leg = route(DALLAS, CHICAGO)

    assert leg.is_estimate
    assert not leg.is_unroutable


def test_route_falls_back_when_osrm_returns_a_degenerate_route(monkeypatch):
    monkeypatch.setattr(
        client.requests,
        "get",
        lambda *a, **k: FakeResponse(
            {"routes": [{"distance": 0, "duration": 0, "geometry": {"coordinates": []}}]}
        ),
    )
    assert route(DALLAS, CHICAGO).is_estimate


def test_estimate_is_never_zero_duration(monkeypatch):
    monkeypatch.setattr(
        client.requests, "get", lambda *a, **k: (_ for _ in ()).throw(requests.Timeout())
    )
    nearby = Place("A", "A", 32.7767, -96.7970)
    assert route(DALLAS, nearby).duration_minutes >= 1


# -- Happy path --------------------------------------------------------------


def test_route_parses_an_osrm_response(monkeypatch):
    monkeypatch.setattr(
        client.requests,
        "get",
        lambda *a, **k: FakeResponse(
            {
                "routes": [
                    {
                        "distance": 1_555_000,  # metres
                        "duration": 61_560,  # seconds
                        "geometry": {"coordinates": [[-96.8, 32.8], [-87.6, 41.9]]},
                    }
                ]
            }
        ),
    )

    leg = route(DALLAS, CHICAGO)

    assert not leg.is_estimate
    assert leg.miles == pytest.approx(966.2, abs=0.5)
    assert leg.duration_minutes == 1026
    assert len(leg.geometry) == 2


def test_geocode_returns_the_top_match(monkeypatch):
    monkeypatch.setattr(
        client.requests,
        "get",
        lambda *a, **k: FakeResponse(
            [
                {
                    "lat": "32.7767",
                    "lon": "-96.7970",
                    "display_name": "Dallas, Dallas County, Texas, 75201, United States",
                }
            ]
        ),
    )

    place = geocode("Dallas TX")

    assert place.latitude == pytest.approx(32.7767)
    assert place.label == "Dallas, Texas"


def test_search_returns_every_candidate_not_just_the_best(monkeypatch):
    """The whole point of the picker: show the driver the Springfields."""
    monkeypatch.setattr(
        client.requests,
        "get",
        lambda *a, **k: FakeResponse(
            [
                {"lat": "39.8", "lon": "-89.6", "display_name": "Springfield, Sangamon County, Illinois, United States"},
                {"lat": "37.2", "lon": "-93.3", "display_name": "Springfield, Greene County, Missouri, United States"},
                {"lat": "42.1", "lon": "-72.6", "display_name": "Springfield, Hampden County, Massachusetts, United States"},
            ]
        ),
    )

    matches = search("Springfield")

    assert [place.label for place in matches] == [
        "Springfield, Illinois",
        "Springfield, Missouri",
        "Springfield, Massachusetts",
    ]


def test_search_asks_nominatim_for_the_requested_limit(monkeypatch):
    seen = {}

    def capture(url, params=None, headers=None, timeout=None):
        seen.update(params or {})
        return FakeResponse([])

    monkeypatch.setattr(client.requests, "get", capture)
    search("Dallas", limit=5)

    assert seen["limit"] == 5


def test_search_returns_empty_for_an_unknown_place(monkeypatch):
    """No matches is a normal answer -- an empty dropdown, not an error."""
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: FakeResponse([]))

    assert search("Nowhere at all, XX") == ()


def test_search_still_raises_when_the_service_is_down(monkeypatch):
    """Distinct from the empty case: the driver should be told to try again."""
    def explode(*args, **kwargs):
        raise requests.ConnectionError("nominatim unreachable")

    monkeypatch.setattr(client.requests, "get", explode)

    with pytest.raises(GeocodingError, match="unavailable"):
        search("Dallas, TX")


# -- Reverse geocoding (pins dropped on the map) -----------------------------


def test_reverse_names_the_place_at_a_coordinate(monkeypatch):
    monkeypatch.setattr(
        client.requests,
        "get",
        lambda *a, **k: FakeResponse(
            {"display_name": "Amarillo, Potter County, Texas, 79101, United States"}
        ),
    )

    place = reverse(35.222, -101.831)

    assert place.label == "Amarillo, Texas"
    # The pin's own coordinates are authoritative, not whatever Nominatim
    # snapped to -- the driver chose that exact spot.
    assert place.latitude == pytest.approx(35.222)
    assert place.longitude == pytest.approx(-101.831)


def test_reverse_asks_for_town_level_not_building_level(monkeypatch):
    seen = {}

    def capture(url, params=None, headers=None, timeout=None):
        seen.update(params or {})
        return FakeResponse({"display_name": "Somewhere, Texas"})

    monkeypatch.setattr(client.requests, "get", capture)
    reverse(35.0, -101.0)

    # Sec. 395.8 wants a city, and the default zoom of 18 returns a building.
    assert seen["zoom"] == client.REVERSE_ZOOM


def test_reverse_degrades_to_a_coordinate_label_when_unavailable(monkeypatch):
    """A pin is already a usable position; a failed name must not block planning."""
    def explode(*args, **kwargs):
        raise requests.ConnectionError("nominatim unreachable")

    monkeypatch.setattr(client.requests, "get", explode)

    place = reverse(35.222, -101.831)

    assert place.label == "35.22, -101.83"
    assert place.latitude == pytest.approx(35.222)


def test_reverse_degrades_when_the_coordinate_has_no_name(monkeypatch):
    # Nominatim answers open ocean with an error object, not a place.
    monkeypatch.setattr(
        client.requests, "get", lambda *a, **k: FakeResponse({"error": "Unable to geocode"})
    )

    assert reverse(0.0, -140.0).label == "0.00, -140.00"


# -- Error handling ----------------------------------------------------------


def test_geocode_rejects_an_empty_query():
    with pytest.raises(GeocodingError, match="cannot be empty"):
        geocode("   ")


def test_geocode_reports_an_unresolvable_location(monkeypatch):
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: FakeResponse([]))

    with pytest.raises(GeocodingError, match="Could not find"):
        geocode("Nowhere at all, XX")


def test_geocode_reports_a_service_outage_distinctly(monkeypatch):
    def explode(*args, **kwargs):
        raise requests.ConnectionError("nominatim unreachable")

    monkeypatch.setattr(client.requests, "get", explode)

    with pytest.raises(GeocodingError, match="unavailable"):
        geocode("Dallas, TX")


def test_geocode_sends_a_descriptive_user_agent(monkeypatch):
    seen = {}

    def capture(url, params=None, headers=None, timeout=None):
        seen.update(headers or {})
        return FakeResponse([{"lat": "1", "lon": "2", "display_name": "A, B, C"}])

    monkeypatch.setattr(client.requests, "get", capture)
    geocode("anywhere")

    # Nominatim rejects requests that do not identify the application.
    assert "spotter" in seen.get("User-Agent", "").lower()


# -- Label trimming ----------------------------------------------------------


@pytest.mark.parametrize(
    "display_name,expected",
    [
        ("Dallas, Dallas County, Texas, 75201, United States", "Dallas, Texas"),
        ("Chicago, Cook County, Illinois, United States", "Chicago, Illinois"),
        ("Houston, Texas", "Houston, Texas"),
    ],
)
def test_labels_are_trimmed_to_fit_the_remarks_column(display_name, expected):
    assert client._short_label(display_name) == expected
