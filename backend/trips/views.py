"""Trip API endpoints."""

from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from routing.client import reverse as reverse_geocode
from routing.client import search as search_places

from .models import Trip
from .serializers import (
    ReplanSerializer,
    TripInputSerializer,
    TripListSerializer,
    TripSerializer,
)
from .services import GeocodingError, ReplanUnavailable, build_trip, replan_trip

logger = logging.getLogger(__name__)

#: Suggestions shown under a location field. Five is what fits without the
#: dropdown needing to scroll.
PLACE_SUGGESTION_LIMIT = 5

#: Reverse lookups are cached on their coordinates, so a dragged marker would
#: miss the cache on every pixel of movement. ~1 m of precision is far finer
#: than a town name needs.
PIN_PRECISION_DP = 5


@api_view(["GET", "POST"])
def trip_collection(request):
    if request.method == "GET":
        trips = Trip.objects.all()[:25]
        return Response(TripListSerializer(trips, many=True).data)

    form = TripInputSerializer(data=request.data)
    form.is_valid(raise_exception=True)

    try:
        trip = build_trip(**form.validated_data)
    except GeocodingError as exc:
        # The driver mistyped a place, or the geocoder is down; either way this
        # is actionable by the user, so surface the message rather than a 500.
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    payload = TripSerializer(trip, context={"request": request}).data
    return Response(payload, status=status.HTTP_201_CREATED)


@api_view(["GET"])
def trip_detail(request, pk: int):
    try:
        trip = Trip.objects.prefetch_related("events").get(pk=pk)
    except Trip.DoesNotExist:
        return Response({"detail": "Trip not found."}, status=status.HTTP_404_NOT_FOUND)

    return Response(TripSerializer(trip, context={"request": request}).data)


@api_view(["POST"])
def trip_replan(request, pk: int):
    """Re-plan a trip with one or more stops moved earlier.

    Creates a new trip rather than editing this one, so both plans survive and
    the driver can see what the change cost. Makes no network calls: the route,
    its geometry and its parking options are all reused from the original.
    """
    try:
        trip = Trip.objects.prefetch_related("events").get(pk=pk)
    except Trip.DoesNotExist:
        return Response({"detail": "Trip not found."}, status=status.HTTP_404_NOT_FOUND)

    form = ReplanSerializer(data=request.data)
    form.is_valid(raise_exception=True)

    try:
        replan = replan_trip(trip, form.validated_data["forced_stops"])
    except ReplanUnavailable as exc:
        # Actionable by the driver -- they can simply plan the trip again -- so
        # this is a 400 with the reason, not a 500.
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    payload = TripSerializer(replan, context={"request": request}).data
    return Response(payload, status=status.HTTP_201_CREATED)


@api_view(["GET"])
def place_search(request):
    """Candidate locations for the picker's dropdown.

    Django proxies this rather than the browser calling Nominatim directly:
    Nominatim requires a descriptive User-Agent, which is a forbidden header
    in browser JS, and going through here lets one cache serve every user.
    """
    query = request.query_params.get("q", "").strip()
    if not query:
        return Response([])

    try:
        matches = search_places(query, limit=PLACE_SUGGESTION_LIMIT)
    except GeocodingError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return Response([_place_payload(place) for place in matches])


@api_view(["GET"])
def place_reverse(request):
    """Name the coordinate a driver just pinned on the map.

    Never fails on a valid coordinate -- the client degrades to a coordinate
    label, because the pin is already a usable position.
    """
    try:
        latitude = round(float(request.query_params["lat"]), PIN_PRECISION_DP)
        longitude = round(float(request.query_params["lon"]), PIN_PRECISION_DP)
    except (KeyError, TypeError, ValueError):
        return Response(
            {"detail": "Both lat and lon are required and must be numbers."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180):
        return Response(
            {"detail": "Coordinates are outside the range of the Earth."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    return Response(_place_payload(reverse_geocode(latitude, longitude)))


def _place_payload(place) -> dict:
    return {"label": place.label, "lat": place.latitude, "lon": place.longitude}


@api_view(["GET"])
def health(request):
    return Response({"status": "ok"})
