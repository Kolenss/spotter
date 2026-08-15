"""API tests.

The routing layer is stubbed throughout: the public OSRM and Nominatim servers
are rate-limited, and stubbing also lets us assert on exact mileage rather than
on whatever the road network happens to return today.
"""

from __future__ import annotations

import pytest

from routing.client import GeocodingError, Place, RouteLeg
from trips import services, views
from trips.models import Trip

pytestmark = pytest.mark.django_db

PLACES = {
    "Dallas, TX": Place("Dallas, TX", "Dallas, Texas", 32.7767, -96.7970),
    "Houston, TX": Place("Houston, TX", "Houston, Texas", 29.7604, -95.3698),
    "Chicago, IL": Place("Chicago, IL", "Chicago, Illinois", 41.8781, -87.6298),
}

#: What reverse geocoding names a pinned coordinate. Anything not listed here
#: stands for a lookup that found nothing, which degrades to the coordinate.
REVERSE = {(41.8781, -87.6298): "Chicago, Illinois"}

PAYLOAD = {
    "current_location": "Dallas, TX",
    "pickup_location": "Houston, TX",
    "dropoff_location": "Chicago, IL",
    "current_cycle_used": 12,
    "start_time": "2026-08-13T06:00:00",
}


@pytest.fixture(autouse=True)
def stub_routing(monkeypatch):
    """Replaces the network layer and records which queries were geocoded.

    Tests that request this fixture get the list of geocoded queries, which is
    how we prove a pinned location skips the lookup entirely.
    """
    geocoded: list[str] = []

    def fake_geocode(query: str) -> Place:
        geocoded.append(query)
        try:
            return PLACES[query]
        except KeyError:
            raise services.GeocodingError(f"Could not find a location matching “{query}”.")

    def fake_route(origin: Place, destination: Place) -> RouteLeg:
        miles = {("Dallas, Texas", "Houston, Texas"): 239.0}.get(
            (origin.label, destination.label), 1083.0
        )
        return RouteLeg(
            miles=miles,
            duration_minutes=round(miles / 58 * 60),
            geometry=[[origin.longitude, origin.latitude], [destination.longitude, destination.latitude]],
            source="osrm",
        )

    def fake_reverse(latitude: float, longitude: float) -> Place:
        label = REVERSE.get((latitude, longitude), f"{latitude:.2f}, {longitude:.2f}")
        return Place(query=label, label=label, latitude=latitude, longitude=longitude)

    monkeypatch.setattr(services, "geocode", fake_geocode)
    monkeypatch.setattr(services, "route", fake_route)
    monkeypatch.setattr(services, "reverse", fake_reverse)
    # Road snapping too. Every waypoint goes through it now, so without this
    # each trip built here would make three live OSRM calls -- and the stubbed
    # coordinates above would silently move, changing the mileage the rest of
    # these tests assert on. Default: already on a road, nothing moved.
    monkeypatch.setattr(services, "snap_to_road", lambda lat, lon: (lat, lon, 0.0))
    # Overpass too, or every trip built here would make a real 12-second call to
    # a public server. Defaults to finding nothing, which is the degraded path
    # the app has to survive anyway; tests that care install their own.
    monkeypatch.setattr(services, "find_along", lambda *args, **kwargs: ())
    yield geocoded


# -- Creating a trip ---------------------------------------------------------


def test_post_creates_a_trip_and_returns_the_full_plan(client):
    response = client.post("/api/trips/", PAYLOAD, content_type="application/json")

    assert response.status_code == 201
    body = response.json()

    assert body["route"]["total_miles"] == pytest.approx(1322.0)
    assert body["summary"]["cycle_used_at_start"] == 12.0
    assert body["daily_logs"]
    assert body["timeline"]
    assert Trip.objects.count() == 1


def test_the_route_reports_why_distances_are_estimated(client, monkeypatch):
    """The UI shows different copy for an outage and an impossible route."""
    def unroutable(origin: Place, destination: Place) -> RouteLeg:
        return RouteLeg(
            miles=9000.0,
            duration_minutes=9000,
            geometry=[[origin.longitude, origin.latitude], [destination.longitude, destination.latitude]],
            source="no-route",
        )

    monkeypatch.setattr(services, "route", unroutable)
    body = client.post("/api/trips/", PAYLOAD, content_type="application/json").json()

    assert body["route"]["distances_estimated"] is True
    assert body["route"]["no_road_route"] is True


def test_a_road_route_flags_neither_estimate_nor_missing_road(client):
    body = client.post("/api/trips/", PAYLOAD, content_type="application/json").json()

    assert body["route"]["distances_estimated"] is False
    assert body["route"]["no_road_route"] is False


def test_every_returned_log_sheet_totals_24_hours(client):
    body = client.post("/api/trips/", PAYLOAD, content_type="application/json").json()

    for sheet in body["daily_logs"]:
        assert sheet["total_hours"] == 24.0
        assert sum(sheet["totals"].values()) == pytest.approx(24.0, abs=0.01)


def test_log_segments_tile_each_day_without_gaps(client):
    body = client.post("/api/trips/", PAYLOAD, content_type="application/json").json()

    for sheet in body["daily_logs"]:
        segments = sheet["segments"]
        assert segments[0]["start_hour"] == 0
        assert segments[-1]["end_hour"] == pytest.approx(24.0)
        for earlier, later in zip(segments, segments[1:]):
            assert earlier["end_hour"] == pytest.approx(later["start_hour"])


# -- Somewhere to actually park ----------------------------------------------


def _facility(name: str, route_miles: float) -> dict:
    return {
        "osm_id": f"node/{abs(hash(name)) % 10_000}",
        "kind": "truck_stop",
        "name": name,
        "lat": 35.0,
        "lon": -95.0,
        "route_miles": route_miles,
        "detour_miles": 0.4,
        "amenities": ["shower"],
    }


class _StubFacility:
    def __init__(self, payload: dict):
        self._payload = payload

    def as_dict(self) -> dict:
        return self._payload


def _two_per_stop(geometry, stops, total, **kwargs):
    """One candidate 30 miles back and one 2 miles back, per real stop marker.

    Built from the markers ``services`` actually hands over rather than from
    fixed numbers, so this also proves the mile markers reaching Overpass are
    the trip's own.
    """
    return tuple(
        _StubFacility(_facility(f"{label} {marker:.0f}", marker - back))
        for marker in stops
        for label, back in (("Early", 30.0), ("Late", 2.0))
    )


def test_each_forced_stop_offers_places_to_park_before_it(client, monkeypatch):
    """Candidates hang off the stops the regulation forced, nearest one first."""
    monkeypatch.setattr(services, "find_along", _two_per_stop)

    body = client.post("/api/trips/", PAYLOAD, content_type="application/json").json()

    parkable = [
        entry
        for entry in body["timeline"]
        if entry["kind"] in {"fuel", "break", "reset", "restart"}
    ]
    assert parkable, "this trip should force at least one stop"

    offered = [entry for entry in parkable if entry["facilities"]]
    assert offered, "a forced stop should carry somewhere to park"

    for entry in offered:
        names = [facility["name"] for facility in entry["facilities"]]
        # Nearest to the stop first -- that is the one costing the least detour.
        distances = [facility["miles_before_stop"] for facility in entry["facilities"]]
        assert distances == sorted(distances)
        assert all(distance >= 0 for distance in distances), (
            "a facility past the marker would mean driving past a clock"
        )
        assert names


def test_the_shipper_and_receiver_are_never_offered_parking(client, monkeypatch):
    """Pickup and drop-off are addresses the driver was given, not a choice."""
    monkeypatch.setattr(services, "find_along", _two_per_stop)

    body = client.post("/api/trips/", PAYLOAD, content_type="application/json").json()

    for entry in body["timeline"]:
        if entry["kind"] in {"pickup", "dropoff", "driving"}:
            assert entry["facilities"] == []


def test_overpass_being_down_still_returns_a_complete_plan(client, monkeypatch):
    """The degraded path, which is the one that has to survive a hosted demo."""
    monkeypatch.setattr(services, "find_along", lambda *args, **kwargs: ())

    response = client.post("/api/trips/", PAYLOAD, content_type="application/json")
    body = response.json()

    assert response.status_code == 201
    assert body["timeline"]
    assert all(entry["facilities"] == [] for entry in body["timeline"])
    for sheet in body["daily_logs"]:
        assert sheet["total_hours"] == 24.0


# -- Moving a stop earlier ----------------------------------------------------


def _plan(client) -> dict:
    return client.post("/api/trips/", PAYLOAD, content_type="application/json").json()


def _replan(client, trip_id: int, route_miles: float, kind: str = "reset"):
    return client.post(
        f"/api/trips/{trip_id}/replan/",
        {"forced_stops": [{"route_miles": route_miles, "kind": kind}]},
        content_type="application/json",
    )


def test_moving_a_rest_earlier_creates_a_second_plan(client):
    """Both survive, so the driver can see what the change cost."""
    original = _plan(client)

    response = _replan(client, original["id"], 300.0)
    replan = response.json()

    assert response.status_code == 201
    assert replan["id"] != original["id"]
    assert replan["replanned_from"] == original["id"]
    assert replan["forced_stops"] == [{"route_miles": 300.0, "kind": "reset"}]
    # The original is untouched and still fetchable.
    assert client.get(f"/api/trips/{original['id']}/").json()["timeline"] == original[
        "timeline"
    ]


def test_the_replan_takes_the_rest_where_it_was_asked_for(client):
    original = _plan(client)

    replan = _replan(client, original["id"], 300.0).json()

    moved = [e for e in replan["timeline"] if "moved earlier" in e["label"]]
    assert len(moved) == 1
    driven = 0.0
    for entry in replan["timeline"]:
        if entry is moved[0]:
            break
        if entry["kind"] == "driving":
            driven += entry["miles"]
    assert driven == pytest.approx(300.0, abs=1.0)


def test_the_replan_is_still_a_valid_set_of_log_sheets(client):
    original = _plan(client)

    replan = _replan(client, original["id"], 300.0).json()

    for sheet in replan["daily_logs"]:
        assert sheet["total_hours"] == 24.0
        assert sum(sheet["totals"].values()) == pytest.approx(24.0, abs=0.01)


def test_the_replan_covers_the_same_distance(client):
    original = _plan(client)

    replan = _replan(client, original["id"], 300.0).json()

    assert replan["route"]["total_miles"] == original["route"]["total_miles"]


def test_replanning_makes_no_network_calls(client, monkeypatch):
    """The route is reused wholesale, so nothing needs looking up again.

    This is what makes a re-plan fast and, more importantly, guarantees the two
    plans are comparable -- a fresh route lookup could return different mileage
    and make the before/after difference meaningless.
    """
    original = _plan(client)

    def explode(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("re-planning must not call the routing services")

    monkeypatch.setattr(services, "route", explode)
    monkeypatch.setattr(services, "geocode", explode)
    monkeypatch.setattr(services, "find_along", explode)

    assert _replan(client, original["id"], 300.0).status_code == 201


def test_the_parking_options_carry_over_to_the_replan(client, monkeypatch):
    monkeypatch.setattr(services, "find_along", _two_per_stop)
    original = _plan(client)

    replan = _replan(client, original["id"], 300.0).json()

    assert any(entry["facilities"] for entry in replan["timeline"])


def test_a_stop_cannot_be_moved_later(client):
    """Only earlier is offered, and the API enforces it rather than trusting.

    A negative mile marker is the nearest thing the wire format has to "later",
    and it is rejected outright.
    """
    original = _plan(client)

    response = _replan(client, original["id"], -50.0)

    assert response.status_code == 400


def test_an_unknown_kind_of_stop_is_rejected(client):
    original = _plan(client)

    assert _replan(client, original["id"], 300.0, kind="lunch").status_code == 400
    # Pickup and drop-off happen at addresses, not at a mile marker.
    assert _replan(client, original["id"], 300.0, kind="pickup").status_code == 400


def test_replanning_a_missing_trip_is_a_404(client):
    assert _replan(client, 999_999, 300.0).status_code == 404


def test_a_trip_predating_the_feature_explains_itself(client):
    """Older rows have no per-leg distances, so they cannot be re-planned."""
    original = _plan(client)
    Trip.objects.filter(pk=original["id"]).update(legs=[])

    response = _replan(client, original["id"], 300.0)

    assert response.status_code == 400
    assert "plan it again" in response.json()["detail"].lower()


def test_pickup_and_dropoff_appear_as_on_duty_stops(client):
    body = client.post("/api/trips/", PAYLOAD, content_type="application/json").json()

    kinds = {entry["kind"]: entry for entry in body["timeline"]}
    assert kinds["pickup"]["status"] == "on_duty"
    assert kinds["pickup"]["duration_hours"] == 1.0
    assert kinds["dropoff"]["status"] == "on_duty"
    assert kinds["dropoff"]["duration_hours"] == 1.0


def test_timeline_interleaves_driving_with_stops_and_is_contiguous(client):
    """A stops-only list reads as though the stops happen back to back.

    The timeline must include the driving legs between them, end to end with
    no gaps, so the plan is legible as an actual journey.
    """
    body = client.post("/api/trips/", PAYLOAD, content_type="application/json").json()
    timeline = body["timeline"]

    assert any(entry["kind"] == "driving" for entry in timeline)
    for earlier, later in zip(timeline, timeline[1:]):
        assert earlier["end"] == later["start"]

    driven = sum(entry["miles"] for entry in timeline if entry["kind"] == "driving")
    assert driven == pytest.approx(body["route"]["total_miles"], abs=1.0)


def test_long_trip_reports_fuel_stops_and_rests(client):
    body = client.post("/api/trips/", PAYLOAD, content_type="application/json").json()

    assert body["summary"]["fuel_stops"] >= 1
    assert body["summary"]["required_rests"] >= 1
    assert body["summary"]["total_days"] >= 2


def test_remarks_carry_a_location_for_each_duty_change(client):
    body = client.post("/api/trips/", PAYLOAD, content_type="application/json").json()

    remarks = [r for sheet in body["daily_logs"] for r in sheet["remarks"]]
    assert remarks
    assert all(r["location"] for r in remarks)
    assert all(0 < r["hour"] <= 24 for r in remarks)


# -- Validation --------------------------------------------------------------


@pytest.mark.parametrize("field", ["current_location", "pickup_location", "dropoff_location"])
def test_blank_locations_are_rejected(client, field):
    payload = {**PAYLOAD, field: ""}
    response = client.post("/api/trips/", payload, content_type="application/json")

    assert response.status_code == 400
    assert field in response.json()


@pytest.mark.parametrize("value", [-1, 70, 85])
def test_cycle_hours_outside_the_legal_range_are_rejected(client, value):
    payload = {**PAYLOAD, "current_cycle_used": value}
    response = client.post("/api/trips/", payload, content_type="application/json")

    assert response.status_code == 400
    assert "current_cycle_used" in response.json()


def test_unresolvable_location_returns_400_not_500(client):
    payload = {**PAYLOAD, "dropoff_location": "Nowhere at all, XX"}
    response = client.post("/api/trips/", payload, content_type="application/json")

    assert response.status_code == 400
    assert "Could not find" in response.json()["detail"]
    assert Trip.objects.count() == 0  # nothing half-written


def test_start_time_is_optional(client):
    payload = {k: v for k, v in PAYLOAD.items() if k != "start_time"}
    response = client.post("/api/trips/", payload, content_type="application/json")

    assert response.status_code == 201


# -- Reading trips back ------------------------------------------------------


def test_trip_detail_matches_what_creation_returned(client):
    created = client.post("/api/trips/", PAYLOAD, content_type="application/json").json()

    fetched = client.get(f"/api/trips/{created['id']}/").json()

    assert fetched["daily_logs"] == created["daily_logs"]
    assert fetched["summary"] == created["summary"]


def test_missing_trip_returns_404(client):
    assert client.get("/api/trips/99999/").status_code == 404


def test_trip_list_returns_recent_trips(client):
    client.post("/api/trips/", PAYLOAD, content_type="application/json")

    body = client.get("/api/trips/").json()

    assert len(body) == 1
    assert body[0]["dropoff_label"] == "Chicago, Illinois"


def test_health_endpoint(client):
    assert client.get("/api/health/").json() == {"status": "ok"}


# -- Picking a location on the map -------------------------------------------


def test_place_search_returns_candidates_for_the_dropdown(client, monkeypatch):
    monkeypatch.setattr(
        views,
        "search_places",
        lambda query, limit: (
            Place("q", "Springfield, Illinois", 39.8, -89.6),
            Place("q", "Springfield, Missouri", 37.2, -93.3),
        ),
    )

    body = client.get("/api/places/search/?q=Springfield").json()

    assert [row["label"] for row in body] == [
        "Springfield, Illinois",
        "Springfield, Missouri",
    ]
    assert body[0]["lat"] == pytest.approx(39.8)


def test_place_search_ignores_a_blank_query_without_calling_out(client, monkeypatch):
    """The field is empty on first render; that must not hit Nominatim."""
    def explode(*args, **kwargs):
        raise AssertionError("should not have called the geocoder")

    monkeypatch.setattr(views, "search_places", explode)

    assert client.get("/api/places/search/?q=   ").json() == []


def test_place_search_surfaces_a_service_outage(client, monkeypatch):
    def explode(query, limit):
        raise GeocodingError("the geocoding service is unavailable")

    monkeypatch.setattr(views, "search_places", explode)

    response = client.get("/api/places/search/?q=Dallas")

    assert response.status_code == 400
    assert "unavailable" in response.json()["detail"]


def test_place_reverse_names_a_pinned_coordinate(client, monkeypatch):
    monkeypatch.setattr(
        views,
        "reverse_geocode",
        lambda lat, lon: Place("pin", "Amarillo, Texas", lat, lon),
    )

    body = client.get("/api/places/reverse/?lat=35.222&lon=-101.831").json()

    assert body["label"] == "Amarillo, Texas"
    assert body["lat"] == pytest.approx(35.222)


def test_place_reverse_rejects_missing_or_unparseable_coordinates(client):
    assert client.get("/api/places/reverse/").status_code == 400
    assert client.get("/api/places/reverse/?lat=35.2").status_code == 400
    assert client.get("/api/places/reverse/?lat=abc&lon=-101.8").status_code == 400


def test_place_reverse_rejects_coordinates_off_the_earth(client):
    response = client.get("/api/places/reverse/?lat=200&lon=-101.8")

    assert response.status_code == 400
    assert "Earth" in response.json()["detail"]


def test_pinned_locations_are_used_as_given_and_never_geocoded(client, stub_routing):
    """The whole point of pinning: the driver's exact point, not a name lookup.

    Re-geocoding the label would risk landing on a different town of the same
    name, quietly moving the trip somewhere they did not choose.
    """
    payload = {
        **PAYLOAD,
        "current_lat": 32.7767,
        "current_lon": -96.7970,
        "pickup_lat": 29.7604,
        "pickup_lon": -95.3698,
        "dropoff_lat": 41.8781,
        "dropoff_lon": -87.6298,
    }

    response = client.post("/api/trips/", payload, content_type="application/json")

    assert response.status_code == 201
    assert stub_routing == [], "a pinned location must not be geocoded"

    waypoints = {point["kind"]: point for point in response.json()["route"]["waypoints"]}
    assert waypoints["dropoff"]["lat"] == pytest.approx(41.8781)


def test_a_pinned_location_can_be_mixed_with_typed_ones(client, stub_routing):
    payload = {**PAYLOAD, "dropoff_lat": 41.8781, "dropoff_lon": -87.6298}

    assert client.post("/api/trips/", payload, content_type="application/json").status_code == 201

    # Only the two typed locations needed a lookup.
    assert stub_routing == ["Dallas, TX", "Houston, TX"]


def test_half_a_coordinate_pair_is_rejected(client):
    """Falling back to the text would place the trip somewhere unchosen."""
    payload = {**PAYLOAD, "dropoff_lat": 41.8781}

    response = client.post("/api/trips/", payload, content_type="application/json")

    assert response.status_code == 400
    assert "together" in str(response.json())


def test_a_coordinate_label_is_named_before_it_is_stored(client):
    """The map pins optimistically; submitting early must not freeze the
    placeholder into the log sheet's Remarks column."""
    payload = {
        **PAYLOAD,
        "dropoff_location": "41.88, -87.63",
        "dropoff_lat": 41.8781,
        "dropoff_lon": -87.6298,
    }

    body = client.post(
        "/api/trips/", payload, content_type="application/json"
    ).json()

    assert body["route"]["dropoff_label"] == "Chicago, Illinois"

    # The name is borrowed; the pin itself does not move.
    dropoff = next(
        w for w in body["route"]["waypoints"] if w["kind"] == "dropoff"
    )
    assert dropoff["lat"] == pytest.approx(41.8781)
    assert dropoff["lon"] == pytest.approx(-87.6298)


def test_an_unnamed_coordinate_falls_back_to_the_coordinate(client):
    """Open ocean has no name. Refusing to plan over it would be absurd."""
    payload = {
        **PAYLOAD,
        "dropoff_location": "0.00, -30.00",
        "dropoff_lat": 0.0,
        "dropoff_lon": -30.0,
    }

    body = client.post(
        "/api/trips/", payload, content_type="application/json"
    ).json()

    assert body["route"]["dropoff_label"] == "0.00, -30.00"


def test_a_real_place_name_is_never_re_looked_up(client, stub_routing):
    """A pinned label that is already a name must be trusted as given --
    re-resolving it could land on a different town of the same name."""
    payload = {
        **PAYLOAD,
        "dropoff_location": "Springfield, Missouri",
        "dropoff_lat": 41.8781,
        "dropoff_lon": -87.6298,
    }

    body = client.post(
        "/api/trips/", payload, content_type="application/json"
    ).json()

    assert body["route"]["dropoff_label"] == "Springfield, Missouri"
    assert "Springfield, Missouri" not in stub_routing


def test_a_pinned_location_still_needs_its_label(client):
    """The label is what the log sheet's Remarks column shows."""
    payload = {
        **PAYLOAD,
        "dropoff_location": "",
        "dropoff_lat": 41.8781,
        "dropoff_lon": -87.6298,
    }

    assert client.post("/api/trips/", payload, content_type="application/json").status_code == 400


# -- Getting the pin onto a road ---------------------------------------------


def test_a_waypoint_off_the_road_network_is_snapped_and_reported(client, monkeypatch):
    """A pin in a field makes the truck profile refuse and the car one accept,
    which is how a trip silently loses its truck routing."""
    def fake_snap(latitude: float, longitude: float):
        # Only the drop-off is off-road, by four kilometres.
        if round(latitude, 2) == 41.88:
            return 41.9, -87.6, 3866.0
        return latitude, longitude, 4.0

    monkeypatch.setattr(services, "snap_to_road", fake_snap)
    payload = {**PAYLOAD, "dropoff_lat": 41.8781, "dropoff_lon": -87.6298}

    body = client.post("/api/trips/", payload, content_type="application/json").json()
    moved = body["route"]["snapped_waypoints"]

    assert [entry["field"] for entry in moved] == ["dropoff"], (
        "only the waypoint that actually moved should be reported"
    )
    assert moved[0]["metres"] == 3866

    dropoff = next(w for w in body["route"]["waypoints"] if w["kind"] == "dropoff")
    assert dropoff["lat"] == pytest.approx(41.9), "the trip must use the snapped point"


def test_a_pin_already_on_a_road_is_not_reported_as_moved(client, monkeypatch):
    """Every pin is snapped; only a move worth mentioning is mentioned."""
    monkeypatch.setattr(services, "snap_to_road", lambda lat, lon: (lat, lon, 4.0))

    body = client.post("/api/trips/", PAYLOAD, content_type="application/json").json()

    assert body["route"]["snapped_waypoints"] == []


def test_a_failed_snap_leaves_the_trip_exactly_as_it_was(client, monkeypatch):
    """OSRM being down must not cost the driver their trip."""
    monkeypatch.setattr(services, "snap_to_road", lambda lat, lon: (lat, lon, 0.0))

    response = client.post("/api/trips/", PAYLOAD, content_type="application/json")

    assert response.status_code == 201
    assert response.json()["route"]["snapped_waypoints"] == []
