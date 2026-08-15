import { useEffect, useState } from "react";
import {
  MapContainer,
  Marker,
  Polyline,
  TileLayer,
  Tooltip,
  useMap,
} from "react-leaflet";
import L, { type DivIcon, type LatLngBoundsExpression } from "leaflet";
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

/* What each stop *is*, at a glance. Seven identical dots in three colours made
   the driver read the legend to tell a fuel stop from a 30-minute break; the
   colours still carry the duty status, and the glyph carries the errand.

   The 10-hour rest and the 34-hour restart are deliberately not both beds:
   they share the sleeper-berth purple, so the glyph is the only thing telling
   them apart, and the 34 is the one that puts the 70-hour cycle back to zero. */
const MARKER_EMOJI: Record<string, string> = {
  start: "🚚",
  pickup: "📦",
  dropoff: "🏁",
  fuel: "⛽",
  break: "☕",
  reset: "🛏️",
  restart: "🔄",
};

/* Built once per kind and size, never per render. react-leaflet calls setIcon()
   whenever the icon's identity changes, which replaces the marker's whole DOM
   node -- rebuilding these inline would recreate every marker on the map on
   each render, and is the same trap documented for the picker's drag handles. */
const iconCache = new Map<string, DivIcon>();

function stopIcon(kind: string, size: number): DivIcon {
  const key = `${kind}:${size}`;
  const cached = iconCache.get(key);
  if (cached) return cached;

  const icon = L.divIcon({
    // Leaflet's own .leaflet-div-icon paints a white box and a border; this
    // class is styled to clear both so only the disc shows.
    className: "stoppin",
    html:
      `<span class="stoppin__disc" style="` +
      `background:${MARKER_COLOR[kind] ?? "#64748b"};` +
      `width:${size}px;height:${size}px;font-size:${Math.round(size * 0.55)}px">` +
      `${MARKER_EMOJI[kind] ?? ""}</span>`,
    iconSize: [size, size],
    // Centred on the point rather than pin-bottomed: these mark a position on
    // a line, not a place on the ground.
    iconAnchor: [size / 2, size / 2],
  });

  iconCache.set(key, icon);
  return icon;
}

function hasPosition(
  entry: TimelineEntry,
): entry is TimelineEntry & { lat: number; lon: number } {
  return entry.lat !== null && entry.lon !== null;
}

/**
 * Click to zoom with the wheel; leave to give the wheel back to the page.
 *
 * The two states this resolves are both real complaints. With wheel zoom off,
 * scrolling over the map does nothing and the page moves underneath it, which
 * is what a driver reads as broken. With it always on, this card -- 420px tall,
 * sitting between the summary and the log sheets -- catches the wheel on the
 * way past and traps the reader.
 *
 * Clicking is the smallest possible statement of "I mean this map". The state
 * it reports drives the hint above the map.
 */
function WheelZoom({ onChange }: { onChange: (live: boolean) => void }) {
  const map = useMap();

  useEffect(() => {
    const container = map.getContainer();

    const enable = () => {
      map.scrollWheelZoom.enable();
      onChange(true);
    };
    const disable = () => {
      map.scrollWheelZoom.disable();
      onChange(false);
    };

    map.on("click", enable);
    // `mouseout` fires when crossing onto a marker or the zoom control, which
    // would flicker the state; the DOM's mouseleave does not.
    container.addEventListener("mouseleave", disable);

    return () => {
      map.off("click", enable);
      container.removeEventListener("mouseleave", disable);
    };
  }, [map, onChange]);

  return null;
}

interface Props {
  trip: Trip;
}

export function TripMap({ trip }: Props) {
  const { path, waypoints } = trip.route;
  /* True while the map owns the mouse wheel. Drives the hint, and nothing else
     -- Leaflet's own handler is the source of truth for the zooming itself. */
  const [wheelLive, setWheelLive] = useState(false);

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
        {/* Rendered conditionally rather than faded out by a class. Two CSS
            approaches -- a `:has()` on a pseudo-element, then a class on the
            wrapper -- both matched their selector and both left the computed
            opacity at 1, so neither could be shown to work. Absence is
            unambiguous, and it is the more honest description anyway: once the
            map has the wheel there is nothing left to say. */}
        {!wheelLive && (
          <div className="map__hint" aria-hidden="true">
            Click the map to zoom with the wheel
          </div>
        )}
        {/* The wheel zooms this map, matching the picker map in the form --
            having one of the two swallow the wheel and the other ignore it was
            the actual complaint. Enabled only once the map is clicked, and
            released again when the pointer leaves: this card sits mid-page with
            the log sheets below it, so a map that grabbed the wheel on hover
            would strand anyone scrolling past it. See WheelZoom below. */}
        <MapContainer
          bounds={bounds}
          scrollWheelZoom={false}
          className="map"
          attributionControl
        >
          <WheelZoom onChange={setWheelLive} />
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
            <Marker
              key={`stop-${index}`}
              position={[entry.lat, entry.lon]}
              icon={stopIcon(entry.kind, 24)}
            >
              <Tooltip direction="top" offset={[0, -14]}>
                <strong>{MARKER_LABEL[entry.kind] ?? entry.label}</strong>
                <br />
                {entry.duration_hours}h &middot; {entry.location}
              </Tooltip>
            </Marker>
          ))}

          {/* Drawn after the mid-route stops so the three the driver actually
              typed sit on top where they overlap. */}
          {waypoints.map((point) => (
            <Marker
              key={point.kind}
              position={[point.lat, point.lon]}
              icon={stopIcon(point.kind, 30)}
            >
              <Tooltip direction="top" offset={[0, -17]} permanent={false}>
                <strong>{MARKER_LABEL[point.kind]}</strong>
                <br />
                {point.label}
              </Tooltip>
            </Marker>
          ))}
        </MapContainer>
      </div>

      <div className="map__legend">
        {(
          ["start", "pickup", "dropoff", "fuel", "break", "reset", "restart"] as const
        ).map((kind) => (
          <span className="legend__item" key={kind}>
            {/* The same disc as the map draws, at the same size, so the legend
                is a key rather than an approximation of one. */}
            <span
              className="stoppin__disc map__key"
              style={{ background: MARKER_COLOR[kind] }}
            >
              {MARKER_EMOJI[kind]}
            </span>
            {MARKER_LABEL[kind]}
          </span>
        ))}
      </div>
    </div>
  );
}
