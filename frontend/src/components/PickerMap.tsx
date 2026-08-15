import { useMemo, useRef, useState } from "react";
import { MapContainer, Marker, TileLayer, Tooltip, useMapEvents } from "react-leaflet";
import L, { type LatLngBoundsExpression } from "leaflet";
import "leaflet/dist/leaflet.css";
import { reverseGeocode } from "../api";
import type { LocationKey, PlaceSuggestion } from "../types";
import { MapFrame } from "./MapFrame";

/* Same palette as the route map, so a pickup pin is the same amber before and
   after the trip is planned. Leaflet writes these through its own JS rather
   than a stylesheet, so they cannot be `var(--…)`; see the note in TripMap. */
const PIN_COLOR: Record<LocationKey, string> = {
  current_location: "#0d1e2d",
  pickup_location: "#d97706",
  dropoff_location: "#d97706",
};

const FIELDS: { key: LocationKey; short: string }[] = [
  { key: "current_location", short: "Current" },
  { key: "pickup_location", short: "Pickup" },
  { key: "dropoff_location", short: "Drop-off" },
];

/** Continental US. The starting view when nothing is pinned yet. */
const DEFAULT_CENTER: [number, number] = [39.5, -98.35];
const DEFAULT_ZOOM = 4;

/* A div icon rather than Leaflet's default marker: the default pulls two PNGs
   by relative URL, which bundlers rewrite and silently break, and a div can be
   coloured to match the rest of the UI.

   Cached per appearance, and deliberately NOT rebuilt per render: react-leaflet
   calls `marker.setIcon()` whenever the icon's identity changes, and setIcon
   replaces the marker's DOM element -- which tears down the drag handler and
   any gesture in progress. Two icons per field is the whole set. */
const ICON_CACHE = new Map<string, L.DivIcon>();

function pinIcon(color: string, active: boolean) {
  const key = `${color}|${active}`;
  let icon = ICON_CACHE.get(key);
  if (!icon) {
    icon = L.divIcon({
      className: "pinicon",
      html: `<span class="${active ? "is-active" : ""}" style="background:${color}"></span>`,
      iconSize: [20, 20],
      iconAnchor: [10, 10],
    });
    ICON_CACHE.set(key, icon);
  }
  return icon;
}

export type PickedPlaces = Partial<Record<LocationKey, PlaceSuggestion>>;

function ClickToPin({ onPick }: { onPick: (lat: number, lon: number) => void }) {
  useMapEvents({
    click: (event) => onPick(event.latlng.lat, event.latlng.lng),
  });
  return null;
}

interface Props {
  picked: PickedPlaces;
  active: LocationKey;
  onActiveChange: (key: LocationKey) => void;
  onResolved: (key: LocationKey, place: PlaceSuggestion) => void;
}

export function PickerMap({ picked, active, onActiveChange, onResolved }: Props) {
  const [resolving, setResolving] = useState<LocationKey | null>(null);

  /* Identifies the newest lookup per field. Nominatim takes about a second, so
     a driver correcting a misplaced pin can easily have two in flight; without
     this the slower first reply would land last and overwrite the second. */
  const sequence = useRef(0);
  const newest = useRef<Partial<Record<LocationKey, number>>>({});

  const pins = FIELDS.map((field) => ({ ...field, place: picked[field.key] })).filter(
    (field): field is typeof field & { place: PlaceSuggestion } =>
      field.place !== undefined,
  );

  const bounds: LatLngBoundsExpression | null = pins.length
    ? pins.map((pin) => [pin.place.lat, pin.place.lon] as [number, number])
    : null;

  // Refit only when a pin is added or removed. Refitting on every coordinate
  // change would fight the driver as they drag a marker.
  const fitKey = pins.map((pin) => pin.key).join(",");

  /**
   * Pin a coordinate now, name it shortly.
   *
   * The reverse lookup goes out to Nominatim and takes about a second. Waiting
   * for it before showing anything makes the map feel broken -- you click, and
   * nothing happens. So the pin lands immediately carrying its own coordinates
   * as a stand-in label, and the place name replaces it when it arrives. The
   * coordinate is the real answer either way; the name is a courtesy, and it is
   * exactly what the server falls back to when the lookup fails.
   */
  function place(key: LocationKey, lat: number, rawLon: number) {
    /* Leaflet keeps counting past the antimeridian, so a click on the second
       copy of the world reports -236 rather than 124. The driver meant the
       place they clicked on, and the API rejects anything outside ±180 -- left
       alone it pins the trip in the Pacific, or fails the plan outright. */
    const lon = ((((rawLon + 180) % 360) + 360) % 360) - 180;

    const token = (sequence.current += 1);
    newest.current[key] = token;

    onResolved(key, { label: `${lat.toFixed(4)}, ${lon.toFixed(4)}`, lat, lon });
    setResolving(key);

    void reverseGeocode(lat, lon).then((named) => {
      // A newer pin for this field has since been dropped; that one wins.
      if (newest.current[key] !== token) return;
      onResolved(key, named);
      setResolving((current) => (current === key ? null : current));
    });
  }

  function handleMapClick(lat: number, lon: number) {
    place(active, lat, lon);

    // Move on to the next field the driver has not filled, so three clicks
    // fill the form without touching the chips. Not awaited -- the pin is
    // already down, and making them wait a second for the chip to advance
    // would put the delay back.
    const next = FIELDS.find(
      (field) => field.key !== active && picked[field.key] === undefined,
    );
    if (next) onActiveChange(next.key);
  }

  const activeLabel = useMemo(
    () => FIELDS.find((field) => field.key === active)?.short ?? "",
    [active],
  );

  return (
    <div className="card">
      <div className="card__head">
        <span className="card__title">Pick locations on the map</span>
        <span className="card__hint">
          {resolving ? "Naming that spot…" : `Click to set ${activeLabel}`}
        </span>
      </div>

      <div className="picker__chips" role="tablist" aria-label="Location being set">
        {FIELDS.map((field) => {
          const place = picked[field.key];
          return (
            <button
              key={field.key}
              type="button"
              role="tab"
              aria-selected={field.key === active}
              className={`chip${field.key === active ? " chip--active" : ""}${
                place ? " chip--set" : ""
              }`}
              onClick={() => onActiveChange(field.key)}
            >
              <span className="chip__dot" style={{ background: PIN_COLOR[field.key] }} />
              <span className="chip__name">{field.short}</span>
              {/* While a name is on its way the chip shows the coordinates it
                  already has, dimmed, so it reads as provisional rather than
                  as the answer. */}
              <span
                className={`chip__place${
                  resolving === field.key ? " chip__place--pending" : ""
                }`}
              >
                {place ? place.label : "not set"}
              </span>
            </button>
          );
        })}
      </div>

      <div className="mapwrap mapwrap--picker">
        <MapContainer
          center={DEFAULT_CENTER}
          zoom={DEFAULT_ZOOM}
          /* Leaflet's default floor is 0, which draws four copies of the world
             side by side with grey above and below -- a view you cannot pick
             anything out of. 3 shows a continent. */
          minZoom={3}
          scrollWheelZoom
          className="map"
          attributionControl
        >
          <TileLayer
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            maxZoom={19}
          />
          <MapFrame bounds={bounds} fitKey={fitKey} />
          <ClickToPin onPick={handleMapClick} />

          {pins.map((pin) => (
            <Marker
              key={pin.key}
              position={[pin.place.lat, pin.place.lon]}
              icon={pinIcon(PIN_COLOR[pin.key], pin.key === active)}
              draggable
              eventHandlers={{
                dragend: (event) => {
                  const { lat, lng } = event.target.getLatLng();
                  place(pin.key, lat, lng);
                },
                click: () => onActiveChange(pin.key),
              }}
            >
              <Tooltip direction="top" offset={[0, -10]}>
                <strong>{pin.short}</strong>
                <br />
                {pin.place.label}
              </Tooltip>
            </Marker>
          ))}
        </MapContainer>
      </div>

      <p className="picker__help">
        Click the map to set the highlighted location, or drag a pin to move it.
        You can also type an address above and choose from the suggestions.
      </p>
    </div>
  );
}
