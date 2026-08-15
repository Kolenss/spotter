import {
  CircleMarker,
  MapContainer,
  Polyline,
  TileLayer,
  Tooltip,
} from "react-leaflet";
import type { LatLngBoundsExpression } from "leaflet";
import "leaflet/dist/leaflet.css";
import { STOP_LABEL, type TimelineEntry, type Trip } from "../types";
import { MapFrame } from "./MapFrame";

/* Marker colours match the timeline and the log grid exactly, so a stop is
   the same colour wherever you meet it in the UI. */
/* Leaflet writes these through its own JS rather than a stylesheet, so they
   cannot be `var(--…)`. This is the one place the tokens in index.css are
   deliberately duplicated — keep the two in sync. */
const MARKER_COLOR: Record<string, string> = {
  start: "#0d1e2d",
  pickup: "#d97706",
  dropoff: "#d97706",
  fuel: "#d97706",
  break: "#64748b",
  reset: "#7c3aed",
  restart: "#7c3aed",
};

/* Shared with the timeline so a stop is named the same in both places. */
const MARKER_LABEL = STOP_LABEL;

function hasPosition(
  entry: TimelineEntry,
): entry is TimelineEntry & { lat: number; lon: number } {
  return entry.lat !== null && entry.lon !== null;
}

interface Props {
  trip: Trip;
}

export function TripMap({ trip }: Props) {
  const { path, waypoints } = trip.route;

  // Mid-route stops only — the pickup and drop-off already have their own
  // geocoded waypoint markers, and drawing both would double them up.
  const stops = trip.timeline
    .filter(hasPosition)
    .filter((entry) => ["fuel", "break", "reset", "restart"].includes(entry.kind));

  const points: [number, number][] = path.length
    ? path
    : waypoints.map((point) => [point.lat, point.lon]);

  if (points.length === 0) {
    return null;
  }

  const bounds: LatLngBoundsExpression = points;

  return (
    <div className="card">
      <div className="card__head">
        <span className="card__title">Route</span>
        <span className="card__hint">
          {trip.route.total_miles.toLocaleString()} mi &middot; {stops.length} stop
          {stops.length === 1 ? "" : "s"} en route
        </span>
      </div>

      <div className="mapwrap">
        <MapContainer
          bounds={bounds}
          scrollWheelZoom={false}
          className="map"
          attributionControl
        >
          <TileLayer
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            maxZoom={19}
          />
          <MapFrame bounds={bounds} fitKey={trip.id} />

          {/* Casing beneath the route line keeps it legible over busy tiles. */}
          <Polyline positions={points} pathOptions={{ color: "#ffffff", weight: 7, opacity: 0.9 }} />
          {/* Deep teal rather than the brand turquoise: #40e0d0 washes out
              against OSM's water and parkland. The white casing above carries
              the contrast either way. */}
          <Polyline positions={points} pathOptions={{ color: "#0f766e", weight: 3.5 }} />

          {stops.map((entry, index) => (
            <CircleMarker
              key={`stop-${index}`}
              center={[entry.lat, entry.lon]}
              radius={5.5}
              pathOptions={{
                color: "#ffffff",
                weight: 2,
                fillColor: MARKER_COLOR[entry.kind] ?? "#64748b",
                fillOpacity: 1,
              }}
            >
              <Tooltip direction="top" offset={[0, -6]}>
                <strong>{MARKER_LABEL[entry.kind] ?? entry.label}</strong>
                <br />
                {entry.duration_hours}h &middot; {entry.location}
              </Tooltip>
            </CircleMarker>
          ))}

          {waypoints.map((point) => (
            <CircleMarker
              key={point.kind}
              center={[point.lat, point.lon]}
              radius={8}
              pathOptions={{
                color: "#ffffff",
                weight: 2.5,
                fillColor: MARKER_COLOR[point.kind],
                fillOpacity: 1,
              }}
            >
              <Tooltip direction="top" offset={[0, -8]} permanent={false}>
                <strong>{MARKER_LABEL[point.kind]}</strong>
                <br />
                {point.label}
              </Tooltip>
            </CircleMarker>
          ))}
        </MapContainer>
      </div>

      <div className="map__legend">
        {(["start", "pickup", "fuel", "break", "reset", "restart"] as const).map(
          (kind) => (
            <span className="legend__item" key={kind}>
              <span
                className="map__dot"
                style={{ background: MARKER_COLOR[kind] }}
              />
              {MARKER_LABEL[kind]}
            </span>
          ),
        )}
      </div>
    </div>
  );
}
