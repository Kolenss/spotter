import type {
  ForcedStop,
  PlaceSuggestion,
  Trip,
  TripListItem,
  TripRequest,
} from "./types";

const BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ?? "http://localhost:8000/api";

/** Field-level validation errors keyed by input name, as DRF returns them. */
export type FieldErrors = Partial<Record<keyof TripRequest, string>>;

export class ApiError extends Error {
  readonly fieldErrors: FieldErrors;

  constructor(message: string, fieldErrors: FieldErrors = {}) {
    super(message);
    this.name = "ApiError";
    this.fieldErrors = fieldErrors;
  }
}

/**
 * Candidate places for the suggestions dropdown.
 *
 * Resolves to an empty list on any failure. A dropdown is an aid, not the only
 * way in -- the driver can always type the location and let the server geocode
 * it -- so a geocoder outage should quietly offer nothing rather than raise an
 * error over an input they are still typing into.
 *
 * Pass an `AbortSignal` so a stale in-flight query cannot overwrite the results
 * of a newer one.
 */
export async function searchPlaces(
  query: string,
  signal?: AbortSignal,
): Promise<PlaceSuggestion[]> {
  try {
    const response = await fetch(
      `${BASE_URL}/places/search/?q=${encodeURIComponent(query)}`,
      { signal },
    );
    if (!response.ok) return [];
    return (await response.json()) as PlaceSuggestion[];
  } catch {
    return [];
  }
}

/**
 * Names the coordinate a driver pinned on the map.
 *
 * Falls back to the coordinate itself if the lookup fails, mirroring the
 * server's own degradation: the pin is already a usable position, so a missing
 * name must not stop them planning the trip.
 */
export async function reverseGeocode(
  lat: number,
  lon: number,
): Promise<PlaceSuggestion> {
  const fallback = { label: `${lat.toFixed(2)}, ${lon.toFixed(2)}`, lat, lon };
  try {
    const response = await fetch(
      `${BASE_URL}/places/reverse/?lat=${lat}&lon=${lon}`,
    );
    if (!response.ok) return fallback;
    return (await response.json()) as PlaceSuggestion;
  } catch {
    return fallback;
  }
}

/**
 * Previously planned trips, newest first.
 *
 * Resolves to an empty list on any failure, like `searchPlaces`. History is a
 * convenience beside the form; losing it must not take the planner down with
 * it, and an empty panel already reads as "nothing here".
 */
export async function listTrips(): Promise<TripListItem[]> {
  try {
    const response = await fetch(`${BASE_URL}/trips/`);
    if (!response.ok) return [];
    return (await response.json()) as TripListItem[];
  } catch {
    return [];
  }
}

/**
 * Re-opens a stored trip.
 *
 * Unlike the list this raises, because the driver clicked a specific row and
 * is owed an explanation if it does not open.
 */
export async function loadTrip(id: number): Promise<Trip> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}/trips/${id}/`);
  } catch {
    throw new ApiError(
      "Could not reach the planning service. Check that the backend is running.",
    );
  }

  if (!response.ok) {
    throw new ApiError(
      response.status === 404
        ? "That trip is no longer available."
        : `Could not open the trip (HTTP ${response.status}).`,
    );
  }

  return (await response.json()) as Trip;
}

/**
 * Re-plan a trip with a stop moved earlier.
 *
 * Returns a new trip rather than editing this one, so the caller can hold both
 * and show what the change cost.
 */
export async function replanTrip(
  id: number,
  forcedStops: ForcedStop[],
): Promise<Trip> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}/trips/${id}/replan/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ forced_stops: forcedStops }),
    });
  } catch {
    throw new ApiError(
      "Could not reach the planning service. Check that the backend is running.",
    );
  }

  if (response.ok) {
    return (await response.json()) as Trip;
  }

  let detail = `Could not move the stop (HTTP ${response.status}).`;
  try {
    const payload = await response.json();
    if (typeof payload?.detail === "string") detail = payload.detail;
  } catch {
    /* keep the status-code message */
  }
  throw new ApiError(detail);
}

export async function planTrip(request: TripRequest): Promise<Trip> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}/trips/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
  } catch {
    throw new ApiError(
      "Could not reach the planning service. Check that the backend is running.",
    );
  }

  if (response.ok) {
    return (await response.json()) as Trip;
  }

  let payload: Record<string, unknown> = {};
  try {
    payload = await response.json();
  } catch {
    throw new ApiError(`Request failed (HTTP ${response.status}).`);
  }

  // DRF returns {detail: "..."} for handled failures and {field: ["..."]} for
  // validation errors; surface the latter next to the input that caused them.
  if (typeof payload.detail === "string") {
    throw new ApiError(payload.detail);
  }

  const fieldErrors: FieldErrors = {};
  for (const [field, messages] of Object.entries(payload)) {
    const text = Array.isArray(messages) ? String(messages[0]) : String(messages);
    fieldErrors[field as keyof TripRequest] = text;
  }

  throw new ApiError("Please correct the highlighted fields.", fieldErrors);
}
