"""Truck-routing tests.

Network-free, like the rest of the routing suite. What matters most here is the
unit conversion: OpenRouteService takes metres and *tonnes*, a US driver states
feet and pounds, and getting weight wrong by a factor of a thousand would route
a 36-tonne combination as though it weighed 36 kg. That bug produces a
perfectly plausible route straight over a weight-limited bridge, so it is worth
more test attention than the happy path.
"""

from __future__ import annotations

import pytest
import requests

from routing import client, ors
from routing.client import Place, route
from routing.ors import TruckRoutingUnavailable, truck_route
from routing.trucks import STANDARD_DRY_VAN, TruckSpec

DALLAS = Place("Dallas, TX", "Dallas, Texas", 32.7767, -96.7970)
CHICAGO = Place("Chicago, IL", "Chicago, Illinois", 41.8781, -87.6298)

#: What ORS returns: GeoJSON, coordinates already [lon, lat].
ORS_PAYLOAD = {
    "features": [
        {
            "properties": {"summary": {"distance": 1_600_000.0, "duration": 90_000.0}},
            "geometry": {
                "type": "LineString",
                "coordinates": [[-96.797, 32.7767], [-92.0, 37.0], [-87.6298, 41.8781]],
            },
        }
    ]
}


@pytest.fixture
def with_key(monkeypatch):
    """Switch truck routing on. Off by default -- see backend/conftest.py."""
    monkeypatch.setenv(ors.API_KEY_ENV, "test-key-123")


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


# -- Units, which is where this would silently go wrong ----------------------


def test_a_standard_dry_van_converts_to_the_units_ors_expects():
    restrictions = STANDARD_DRY_VAN.as_ors_restrictions()

    # 13'6" is 4.11 m, 53 ft is 16.15 m, 8'6" is 2.59 m.
    assert restrictions["height"] == pytest.approx(4.11, abs=0.01)
    assert restrictions["length"] == pytest.approx(16.15, abs=0.02)
    assert restrictions["width"] == pytest.approx(2.59, abs=0.01)
    # 80,000 lb is 36.29 *tonnes*. Kilograms here would be 36,287 and would
    # route the truck as though it were featherweight.
    assert restrictions["weight"] == pytest.approx(36.29, abs=0.02)
    assert 30 < restrictions["weight"] < 40, "weight must be tonnes, not kg or lb"
    assert restrictions["axleload"] == pytest.approx(7.71, abs=0.02)
    assert restrictions["hazmat"] is False


def test_hazmat_is_carried_through():
    assert TruckSpec(hazmat=True).as_ors_restrictions()["hazmat"] is True


def test_the_spec_describes_itself_in_the_units_a_driver_uses():
    assert STANDARD_DRY_VAN.describe() == "53ft trailer, 13'6\" high, 80,000 lb, 5 axles"


# -- The request -------------------------------------------------------------


def test_the_request_asks_for_the_truck_profile_with_the_restrictions(monkeypatch):
    sent = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent["url"] = url
        sent["body"] = json
        sent["headers"] = headers
        return FakeResponse(ORS_PAYLOAD)

    monkeypatch.setattr(ors.requests, "post", fake_post)

    truck_route(-96.797, 32.7767, -87.6298, 41.8781, STANDARD_DRY_VAN, "key-abc")

    assert "driving-hgv" in sent["url"]
    # Bare key, no "Bearer" prefix -- ORS's own examples omit it.
    assert sent["headers"]["Authorization"] == "key-abc"
    assert sent["body"]["coordinates"] == [[-96.797, 32.7767], [-87.6298, 41.8781]]
    restrictions = sent["body"]["options"]["profile_params"]["restrictions"]
    assert restrictions["height"] == pytest.approx(4.11, abs=0.01)


def test_geojson_geometry_is_used_as_is(monkeypatch):
    """ORS speaks GeoJSON, which is already how route_geometry is stored.

    No decoding and no transposing -- if this ever starts needing either, the
    coordinates are the wrong way round somewhere.
    """
    monkeypatch.setattr(ors.requests, "post", lambda *a, **k: FakeResponse(ORS_PAYLOAD))

    miles, minutes, geometry = truck_route(
        -96.797, 32.7767, -87.6298, 41.8781, STANDARD_DRY_VAN, "key"
    )

    assert miles == pytest.approx(994.2, abs=0.5)  # 1,600 km
    assert minutes == 1500  # 90,000 s
    assert geometry[0] == [-96.797, 32.7767]
    assert all(-180 <= point[0] <= 180 for point in geometry), "first value is longitude"


# -- Failure -----------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"features": []},
        {"features": [{"properties": {"summary": {}}, "geometry": {"coordinates": []}}]},
        {
            "features": [
                {
                    "properties": {"summary": {"distance": 0, "duration": 0}},
                    "geometry": {"coordinates": [[1, 2], [3, 4]]},
                }
            ]
        },
        {},
    ],
)
def test_an_unusable_answer_is_reported_as_unavailable(monkeypatch, payload):
    monkeypatch.setattr(ors.requests, "post", lambda *a, **k: FakeResponse(payload))

    with pytest.raises(TruckRoutingUnavailable):
        truck_route(-96.8, 32.8, -87.6, 41.9, STANDARD_DRY_VAN, "key")


def test_a_rejected_key_is_reported_as_unavailable(monkeypatch):
    monkeypatch.setattr(
        ors.requests, "post", lambda *a, **k: FakeResponse({"error": "nope"}, status=403)
    )

    with pytest.raises(TruckRoutingUnavailable):
        truck_route(-96.8, 32.8, -87.6, 41.9, STANDARD_DRY_VAN, "bad-key")


# -- The dispatcher ----------------------------------------------------------


def test_without_a_key_nothing_changes(monkeypatch):
    """The whole feature has to be inert until a key exists."""
    def no_truck_calls(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("ORS was called with no key configured")

    monkeypatch.setattr(ors.requests, "post", no_truck_calls)
    monkeypatch.setattr(
        client.requests,
        "get",
        lambda *a, **k: FakeResponse(
            {
                "routes": [
                    {
                        "distance": 1_600_000.0,
                        "duration": 90_000.0,
                        "geometry": {"coordinates": [[-96.8, 32.8], [-87.6, 41.9]]},
                    }
                ]
            }
        ),
    )

    leg = route(DALLAS, CHICAGO)

    assert leg.source == "osrm"
    assert leg.is_truck_legal is False


def test_with_a_key_the_truck_route_wins(monkeypatch, with_key):
    monkeypatch.setattr(ors.requests, "post", lambda *a, **k: FakeResponse(ORS_PAYLOAD))

    def no_osrm(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("OSRM was called despite a truck route succeeding")

    monkeypatch.setattr(client.requests, "get", no_osrm)

    leg = route(DALLAS, CHICAGO)

    assert leg.source == "ors"
    assert leg.is_truck_legal is True
    assert leg.is_estimate is False


def test_ors_failing_falls_back_to_the_car_route(monkeypatch, with_key):
    """A car route is wrong about bridges but right about roads.

    Better than refusing to plan, and it is flagged so the driver is not told
    a car route is truck-legal.
    """
    def explode(*args, **kwargs):
        raise requests.ConnectionError("ors unreachable")

    monkeypatch.setattr(ors.requests, "post", explode)
    monkeypatch.setattr(
        client.requests,
        "get",
        lambda *a, **k: FakeResponse(
            {
                "routes": [
                    {
                        "distance": 1_600_000.0,
                        "duration": 90_000.0,
                        "geometry": {"coordinates": [[-96.8, 32.8], [-87.6, 41.9]]},
                    }
                ]
            }
        ),
    )

    leg = route(DALLAS, CHICAGO)

    assert leg.source == "osrm"
    assert leg.is_truck_legal is False


def test_both_routers_failing_still_degrades_to_an_estimate(monkeypatch, with_key):
    def explode(*args, **kwargs):
        raise requests.ConnectionError("everything is down")

    monkeypatch.setattr(ors.requests, "post", explode)
    monkeypatch.setattr(client.requests, "get", explode)

    leg = route(DALLAS, CHICAGO)

    assert leg.is_estimate
    assert leg.is_truck_legal is False
    assert leg.miles > 0
