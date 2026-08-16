"""Persisted trips and their computed duty timelines.

Daily log sheets are deliberately *not* stored. They are derived from the duty
events at serialization time, so a sheet can never drift out of sync with the
timeline it is supposed to depict.
"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from hos.rules import DutyStatus


class Trip(models.Model):
    """The four inputs, plus everything the routing layer resolved from them."""

    current_location = models.CharField(max_length=255)
    pickup_location = models.CharField(max_length=255)
    dropoff_location = models.CharField(max_length=255)

    #: "Current Cycle Used (Hrs)" -- hours already worked against the 70-hour,
    #: 8-day limit when the trip begins.
    current_cycle_used = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(70)],
    )

    start_time = models.DateTimeField()

    # Resolved by the geocoder; labels are what appear in the Remarks column.
    current_label = models.CharField(max_length=255, blank=True)
    pickup_label = models.CharField(max_length=255, blank=True)
    dropoff_label = models.CharField(max_length=255, blank=True)

    # Waypoint coordinates, so the map can place markers without re-geocoding.
    current_lat = models.FloatField(null=True, blank=True)
    current_lon = models.FloatField(null=True, blank=True)
    pickup_lat = models.FloatField(null=True, blank=True)
    pickup_lon = models.FloatField(null=True, blank=True)
    dropoff_lat = models.FloatField(null=True, blank=True)
    dropoff_lon = models.FloatField(null=True, blank=True)

    total_distance_miles = models.FloatField(default=0)
    total_duration_minutes = models.IntegerField(default=0)

    #: Per-leg [{"miles": .., "minutes": ..}] for current->pickup and
    #: pickup->dropoff. The totals above are the sum of these; keeping the
    #: split means a leg's own mileage is recoverable without re-routing.
    legs = models.JSONField(default=list, blank=True)

    #: GeoJSON coordinate pairs for the whole route.
    route_geometry = models.JSONField(default=list, blank=True)

    #: True whenever distances came from the haversine fallback rather than a
    #: road route, for either reason below.
    distances_estimated = models.BooleanField(default=False)

    #: True when a truck profile produced this route, honouring height, weight,
    #: length and axle load. False means it came from a car profile or a
    #: straight line and may cross a low bridge or a road that bans lorries.
    #: Stored per trip because it depends on whether a key was configured when
    #: the trip was planned, which can change between one trip and the next.
    truck_routed = models.BooleanField(default=False)

    #: True when the routing service answered that no drivable road path exists
    #: between the waypoints -- a disconnected road graph, not an outage. Kept
    #: separate because retrying fixes one and never fixes the other.
    no_road_route = models.BooleanField(default=False)

    #: Waypoints moved onto the nearest road before routing, as
    #: [{"field": "dropoff", "label": .., "metres": ..}]. Only ones moved far
    #: enough to be worth mentioning appear here.
    #:
    #: Recorded rather than silently applied because this relocates the
    #: driver's own choice, sometimes by kilometres -- the same reason a
    #: half-supplied coordinate pair is a 400 rather than a guess. A drop-off
    #: that moved four kilometres onto a forest track is something the driver
    #: needs to see, not a detail to bury.
    snapped_waypoints = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.current_label or self.current_location} to {self.dropoff_label or self.dropoff_location}"


class DutyEvent(models.Model):
    """One continuous block of a single duty status within a trip."""

    STATUS_CHOICES = [(status.value, status.name.title()) for status in DutyStatus]

    trip = models.ForeignKey(Trip, related_name="events", on_delete=models.CASCADE)
    sequence = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    location = models.CharField(max_length=255, blank=True)
    note = models.CharField(max_length=255, blank=True)
    miles = models.FloatField(default=0)

    class Meta:
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["trip", "sequence"], name="unique_event_sequence_per_trip"
            )
        ]

    def __str__(self) -> str:
        return f"{self.status} {self.start_at:%m/%d %H:%M}-{self.end_at:%H:%M}"
