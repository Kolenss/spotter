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
