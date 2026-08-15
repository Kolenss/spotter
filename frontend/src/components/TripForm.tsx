import { useState, type FormEvent } from "react";
import type { FieldErrors } from "../api";
import type { LocationKey, PlaceSuggestion, TripRequest } from "../types";
import { LocationField } from "./LocationField";
import type { PickedPlaces } from "./PickerMap";

interface Props {
  onSubmit: (request: TripRequest) => void;
  loading: boolean;
  fieldErrors: FieldErrors;
  /** Locations resolved by the picker map, owned by App so both stay in step. */
  picked: PickedPlaces;
  onPicked: (key: LocationKey, place: PlaceSuggestion | null) => void;
  active: LocationKey;
  onActiveChange: (key: LocationKey) => void;
}

const FIELDS: {
  key: LocationKey;
  label: string;
  placeholder: string;
  note: string;
}[] = [
  {
    key: "current_location",
    label: "Current location",
    placeholder: "Dallas, TX",
    note: "Where the driver is right now.",
  },
  {
    key: "pickup_location",
    label: "Pickup location",
    placeholder: "Houston, TX",
    note: "1 hour is logged on duty here.",
  },
  {
    key: "dropoff_location",
    label: "Drop-off location",
    placeholder: "Chicago, IL",
    note: "1 hour is logged on duty here.",
  },
];

/** Which request fields a resolved location writes its coordinates into. */
const COORD_KEYS: Record<LocationKey, { lat: keyof TripRequest; lon: keyof TripRequest }> = {
  current_location: { lat: "current_lat", lon: "current_lon" },
  pickup_location: { lat: "pickup_lat", lon: "pickup_lon" },
  dropoff_location: { lat: "dropoff_lat", lon: "dropoff_lon" },
};

export function TripForm({
  onSubmit,
  loading,
  fieldErrors,
  picked,
  onPicked,
  active,
  onActiveChange,
}: Props) {
  const [cycleUsed, setCycleUsed] = useState(0);
  const [typed, setTyped] = useState<Record<LocationKey, string>>({
    current_location: "",
    pickup_location: "",
    dropoff_location: "",
  });
  const [touched, setTouched] = useState(false);

  // A picked place supplies the text as well as the point, so it wins until
  // the driver types over it -- which clears the pick.
  const valueFor = (key: LocationKey) => picked[key]?.label ?? typed[key];

  function handleType(key: LocationKey, value: string) {
    setTyped((current) => ({ ...current, [key]: value }));
    // Dropping the pin is the whole point: the words no longer describe that
    // coordinate, and silently keeping it would plan a trip to somewhere the
    // driver did not ask for.
    if (picked[key]) onPicked(key, null);
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setTouched(true);

    if (loading) return;
    if (FIELDS.some((field) => valueFor(field.key).trim() === "")) return;

    const request: TripRequest = {
      current_location: valueFor("current_location").trim(),
      pickup_location: valueFor("pickup_location").trim(),
      dropoff_location: valueFor("dropoff_location").trim(),
      current_cycle_used: cycleUsed,
    };

    for (const field of FIELDS) {
      const place = picked[field.key];
      if (!place) continue;
      const { lat, lon } = COORD_KEYS[field.key];
      Object.assign(request, { [lat]: place.lat, [lon]: place.lon });
    }

    onSubmit(request);
  }

  return (
    <form className="card" onSubmit={handleSubmit} noValidate>
      <div className="card__head">
        <span className="card__title">Trip details</span>
        <span className="card__hint">Property-carrying &middot; 70 hr / 8 day</span>
      </div>

      <div className="card__body">
        {FIELDS.map((field) => (
          <LocationField
            key={field.key}
            id={field.key}
            label={field.label}
            placeholder={field.placeholder}
            note={field.note}
            value={valueFor(field.key)}
            pinned={picked[field.key] ?? null}
            error={fieldErrors[field.key]}
            showRequired={touched && valueFor(field.key).trim() === ""}
            active={active === field.key}
            onType={(value) => handleType(field.key, value)}
            onPick={(place) => onPicked(field.key, place)}
            onFocus={() => onActiveChange(field.key)}
          />
        ))}

        <div className="field">
          <label className="field__label" htmlFor="current_cycle_used">
            Current cycle used (hrs)
          </label>
          <input
            id="current_cycle_used"
            type="number"
            min={0}
            max={69.75}
            step={0.25}
            value={cycleUsed}
            aria-invalid={Boolean(fieldErrors.current_cycle_used)}
            aria-describedby="cycle-note"
            onChange={(event) => setCycleUsed(Number(event.target.value))}
          />
          <span className="field__note" id="cycle-note">
            On-duty hours already worked in the last 8 days, out of 70.
          </span>
          {fieldErrors.current_cycle_used && (
            <span className="field__error">{fieldErrors.current_cycle_used}</span>
          )}
        </div>

        <button className="btn" type="submit" disabled={loading}>
          {loading ? "Planning route…" : "Plan trip & draw logs"}
        </button>
      </div>
    </form>
  );
}
