from django.urls import path

from . import views

urlpatterns = [
    path("trips/", views.trip_collection, name="trip-collection"),
    path("trips/<int:pk>/", views.trip_detail, name="trip-detail"),
    path("places/search/", views.place_search, name="place-search"),
    path("places/reverse/", views.place_reverse, name="place-reverse"),
    path("health/", views.health, name="health"),
]
