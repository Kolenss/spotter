import type {
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
 * it. Used for the cosmetic refreshes after planning and re-planning, where
 * the result the driver came for is already on screen.
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

//: A sleeping free-tier service takes 30-50s to wake, so this has to outlast
//: that or the retry is theatre. Five attempts 5s apart covers ~25s of waking
//: plus however long the requests themselves take.
const WAKE_ATTEMPTS = 5;
const WAKE_DELAY_MS = 5000;

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * History for the first paint, which is the one request that has to survive a
 * cold start.
 *
 * The free Render tier spins the API down after 15 minutes idle. A request
 * arriving during the wake-up is answered by the *platform*, not by Django --
 * and that error page carries no CORS headers, so the browser reports it as
 * "No 'Access-Control-Allow-Origin' header is present" and `fetch` rejects
 * with a bare TypeError. It looks exactly like a misconfigured backend and is
 * nothing of the sort, so the only honest response is to wait and ask again.
 *
 * Unlike `listTrips` this throws once the attempts are spent. Silently
 * resolving to `[]` is what made a sleeping server indistinguishable from an
 * account with no trips in it: the panel said "trips you plan are saved here"
 * while the trips sat safely in the database.
 *
 * `onWaking` fires once the first attempt has failed, so the caller can say
 * what is happening rather than leave a spinner with no explanation.
 */
export async function loadHistory(
  onWaking?: () => void,
): Promise<TripListItem[]> {
  for (let attempt = 1; attempt <= WAKE_ATTEMPTS; attempt += 1) {
    try {
      const response = await fetch(`${BASE_URL}/trips/`);
      if (response.ok) return (await response.json()) as TripListItem[];
      // 5xx is the platform or a broken deploy, and may pass; 4xx is a real
      // answer about this request and retrying it just wastes the driver's time.
      if (response.status < 500) return [];
    } catch {
      /* unreachable -- fall through to the retry */
    }

    if (attempt === 1) onWaking?.();
    if (attempt < WAKE_ATTEMPTS) await wait(WAKE_DELAY_MS);
  }

  throw new ApiError(
    "Could not reach the planning service, so previous trips could not be loaded.",
  );
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
