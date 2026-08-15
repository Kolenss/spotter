"""Facility lookup tests.

Network-free like the rest of the routing suite: Overpass is a free public
server with a concurrency cap, and a suite that hammered it would be both rude
and flaky.

The route used throughout is a straight north-south line at longitude -100 from
latitude 40 to 41 -- 69.1 miles, one degree of latitude -- so every distance in
these tests can be checked by hand.
"""

from __future__ import annotations

import pytest
import requests

from routing import facilities
from routing.facilities import CORRIDOR_WIDTH_MILES, find_along

#: 0.01 degrees of latitude per step, ~0.69 mi, 101 vertices, ~69.1 mi total.
ROUTE = [[-100.0, 40.0 + step * 0.01] for step in range(101)]
ROUTE_MILES = 69.09


@pytest.fixture(autouse=True)
def clear_lookup_state():
    """Reset both pieces of module state between tests.

    The cache is the obvious one. The failure backoff is the one that bites:
    it is a module-level timestamp, so a test that simulates an outage leaves
    every later test suppressed for the next sixty seconds. In isolation they
    all pass, which is exactly what makes it confusing to chase.
    """
    def reset():
        facilities._fetch.cache_clear()
        facilities._blocked_until = 0.0

    reset()
    yield
    reset()


def node(osm_id: int, lat: float, lon: float, **tags) -> dict:
    return {"type": "node", "id": osm_id, "lat": lat, "lon": lon, "tags": tags}


def respond_with(monkeypatch, elements, recorder: list | None = None):
    """Stub Overpass, optionally recording the queries it was sent."""

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"elements": elements}

    def fake_post(url, data=None, headers=None, timeout=None):
        assert headers and headers.get("User-Agent"), (
            "Overpass mirrors rate-limit anonymous requests with a 429"
        )
        if recorder is not None:
            recorder.append(data["data"])
        return FakeResponse()

    monkeypatch.setattr(facilities.requests, "post", fake_post)


# -- The safety rule ---------------------------------------------------------


def test_a_facility_past_the_stop_is_never_offered(monkeypatch):
    """Backwards only. Driving past the marker is driving past a clock.

    The truck stop here sits at ~55 mi on a route whose stop is at 40 mi. It is
    excluded because the corridor searched ends at the marker, so the nearest
    searched point is 15 miles away -- far outside the corridor width.
    """
    respond_with(monkeypatch, [node(1, 40.8, -100.0, highway="services")])

    found = find_along(ROUTE, [40.0], ROUTE_MILES)

    assert found == ()


def test_a_facility_before_the_stop_is_offered_with_its_mile_marker(monkeypatch):
    respond_with(monkeypatch, [node(1, 40.5, -100.0, highway="services", name="Pilot")])

    (facility,) = find_along(ROUTE, [40.0], ROUTE_MILES)

    assert facility.name == "Pilot"
    assert facility.kind == "truck_stop"
    # Half a degree of latitude along a 69.1 mile route.
    assert 34.0 < facility.route_miles < 35.0
    assert facility.route_miles <= 40.0


def test_a_facility_off_the_route_is_rejected(monkeypatch):
    """Two candidates at the same mile marker, one 1.1 mi off and one 2.6 mi."""
    respond_with(
        monkeypatch,
        [
            node(1, 40.5, -99.98, highway="services", name="Close"),
            node(2, 40.5, -99.95, highway="services", name="Far"),
        ],
    )

    found = find_along(ROUTE, [40.0], ROUTE_MILES)

    assert [facility.name for facility in found] == ["Close"]
    assert found[0].detour_miles <= CORRIDOR_WIDTH_MILES


# -- Degradation -------------------------------------------------------------


def test_an_overpass_outage_costs_facilities_not_the_trip(monkeypatch):
    def explode(*args, **kwargs):
        raise requests.ConnectionError("overpass unreachable")

    monkeypatch.setattr(facilities.requests, "post", explode)

    assert find_along(ROUTE, [40.0], ROUTE_MILES) == ()


def test_a_timeout_degrades_the_same_way(monkeypatch):
    def stall(*args, **kwargs):
        raise requests.Timeout("overpass is busy")

    monkeypatch.setattr(facilities.requests, "post", stall)

    assert find_along(ROUTE, [40.0], ROUTE_MILES) == ()


def test_a_dead_mirror_falls_through_to_a_live_one(monkeypatch):
    """The busiest endpoint refuses connections outright when saturated.

    It was unreachable from the development machine for the whole of this
    build while the mirrors answered, so this is the common case, not the
    exotic one.
    """
    attempted: list[str] = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"elements": [node(1, 40.5, -100.0, highway="services", name="Sapp Bros")]}

    def fake_post(url, data=None, headers=None, timeout=None):
        attempted.append(url)
        if len(attempted) == 1:
            raise requests.ConnectTimeout("main endpoint saturated")
        return FakeResponse()

    monkeypatch.setattr(facilities.requests, "post", fake_post)

    (facility,) = find_along(ROUTE, [40.0], ROUTE_MILES)

    assert facility.name == "Sapp Bros"
    assert len(attempted) == 2, "should stop at the first mirror that answers"


def test_a_mirror_serving_an_html_error_page_is_skipped(monkeypatch):
    """Some mirrors answer 200 with HTML when overloaded, not JSON."""
    attempted: list[str] = []

    class Good:
        def raise_for_status(self):
            return None

        def json(self):
            return {"elements": [node(1, 40.5, -100.0, highway="rest_area")]}

    class HtmlErrorPage:
        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("Expecting value: line 1 column 1")

    def fake_post(url, data=None, headers=None, timeout=None):
        attempted.append(url)
        return HtmlErrorPage() if len(attempted) == 1 else Good()

    monkeypatch.setattr(facilities.requests, "post", fake_post)

    assert len(find_along(ROUTE, [40.0], ROUTE_MILES)) == 1
    assert len(attempted) == 2


def test_a_nonsense_payload_degrades_rather_than_raising(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return "not what overpass returns"

    monkeypatch.setattr(
        facilities.requests, "post", lambda *a, **k: FakeResponse()
    )

    assert find_along(ROUTE, [40.0], ROUTE_MILES) == ()


def test_no_stops_means_no_lookup(monkeypatch):
    def explode(*args, **kwargs):  # pragma: no cover - must never be reached
        raise AssertionError("Overpass was called with nothing to look up")

    monkeypatch.setattr(facilities.requests, "post", explode)

    assert find_along(ROUTE, [], ROUTE_MILES) == ()
    assert find_along([], [40.0], ROUTE_MILES) == ()
    assert find_along(ROUTE, [40.0], 0) == ()


# -- Parsing -----------------------------------------------------------------


def test_an_area_is_read_from_its_centre(monkeypatch):
    """Services and rest areas are usually mapped as ways, not points.

    ``out center`` is what gives them a coordinate; without reading it they
    would all be silently dropped.
    """
    respond_with(
        monkeypatch,
        [
            {
                "type": "way",
                "id": 42,
                "center": {"lat": 40.5, "lon": -100.0},
                "tags": {"highway": "rest_area", "name": "Platte River Rest Area"},
            }
        ],
    )

    (facility,) = find_along(ROUTE, [40.0], ROUTE_MILES)

    assert facility.osm_id == "way/42"
    assert facility.kind == "rest_area"


def test_untagged_and_car_only_features_are_ignored(monkeypatch):
    """A forecourt with no hgv tag cannot take an 80,000 lb combination."""
    respond_with(
        monkeypatch,
        [
            node(1, 40.5, -100.0, amenity="fuel"),
            node(2, 40.5, -100.0, amenity="parking"),
            node(3, 40.5, -100.0),
            node(4, 40.5, -100.0, amenity="fuel", hgv="no", name="Walmart"),
            node(5, 40.5, -100.0, amenity="fuel", hgv="yes", name="Love's"),
        ],
    )

    found = find_along(ROUTE, [40.0], ROUTE_MILES)

    assert [facility.name for facility in found] == ["Love's"]
    assert found[0].kind == "fuel"


def test_hgv_designated_counts_as_truck_legal(monkeypatch):
    """`designated` is the common US spelling, and the stronger of the two.

    Measured against a box of I-80 in Nebraska, seven truck fuel stations were
    tagged `designated` and none `yes`. Accepting only `yes` found nothing.
    """
    respond_with(
        monkeypatch,
        [
            node(1, 40.5, -100.0, amenity="fuel", hgv="designated", name="Flying J"),
            node(2, 40.5, -100.0, amenity="parking", hgv="designated", name="Truck lot"),
        ],
    )

    found = find_along(ROUTE, [40.0], ROUTE_MILES)

    assert {facility.name for facility in found} == {"Flying J", "Truck lot"}


def test_the_query_accepts_both_hgv_spellings(monkeypatch):
    queries: list[str] = []
    respond_with(monkeypatch, [], recorder=queries)

    find_along(ROUTE, [40.0], ROUTE_MILES)

    assert '"hgv"~"^(yes|designated)$"' in queries[0]
    # Service areas take trucks by definition and must not be hgv-filtered --
    # it is how TA and Flying J travel centres are mapped.
    services = queries[0].split('nwr["highway"="services"]')[1].split(";")[0]
    assert "hgv" not in services


def test_an_unnamed_feature_falls_back_to_its_kind(monkeypatch):
    respond_with(monkeypatch, [node(1, 40.5, -100.0, highway="rest_area")])

    (facility,) = find_along(ROUTE, [40.0], ROUTE_MILES)

    assert facility.name == "Rest area"


def test_the_amenities_a_driver_actually_asks_about_are_kept(monkeypatch):
    respond_with(
        monkeypatch,
        [
            node(
                1,
                40.5,
                -100.0,
                highway="services",
                name="TA",
                shower="yes",
                toilets="yes",
                internet_access="wlan",
                surface="asphalt",
            )
        ],
    )

    (facility,) = find_along(ROUTE, [40.0], ROUTE_MILES)

    assert set(facility.amenities) == {"shower", "toilets", "wifi"}


# -- Query shape -------------------------------------------------------------


def test_overlapping_corridors_are_merged_into_one_box(monkeypatch):
    """Two stops 10 miles apart share a 50-mile lookback almost entirely.

    Four tag statements means one bounding box; eight would mean the overlap was
    queried twice, which is exactly the waste that gets a client throttled.
    """
    queries: list[str] = []
    respond_with(monkeypatch, [], recorder=queries)

    find_along(ROUTE, [30.0, 40.0], ROUTE_MILES)

    assert queries[0].count("nwr") == 4


def test_every_stop_is_searched_when_corridors_do_not_overlap(monkeypatch):
    queries: list[str] = []
    respond_with(monkeypatch, [], recorder=queries)

    find_along(ROUTE, [10.0, 65.0], ROUTE_MILES, lookback_miles=5.0)

    assert queries[0].count("nwr") == 8


def test_the_query_asks_for_all_four_kinds_and_their_centres(monkeypatch):
    queries: list[str] = []
    respond_with(monkeypatch, [], recorder=queries)

    find_along(ROUTE, [40.0], ROUTE_MILES)

    query = queries[0]
    assert '"highway"="services"' in query
    assert '"highway"="rest_area"' in query
    assert '"amenity"="fuel"' in query
    assert '"amenity"="parking"' in query
    assert "out center tags" in query


def test_an_identical_route_is_only_looked_up_once(monkeypatch):
    queries: list[str] = []
    respond_with(monkeypatch, [], recorder=queries)

    find_along(ROUTE, [40.0], ROUTE_MILES)
    find_along(ROUTE, [40.0], ROUTE_MILES)

    assert len(queries) == 1


# -- Road miles vs polyline miles --------------------------------------------


def test_mile_markers_come_back_in_the_road_miles_the_engine_uses(monkeypatch):
    """The polyline is a great-circle line; the road is longer and winds.

    Reporting a facility's position in polyline miles would put it in a
    different unit from the stop marker it is meant to be compared against.
    Here the road is twice the straight line, so a facility halfway along the
    polyline must report roughly halfway along the *road*.
    """
    respond_with(monkeypatch, [node(1, 40.5, -100.0, highway="services")])

    (facility,) = find_along(ROUTE, [80.0], ROUTE_MILES * 2)

    assert 68.0 < facility.route_miles < 70.0
