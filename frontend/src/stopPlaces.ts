/**
 * Real place names for stops the engine could only describe as "en route".
 *
 * The HOS engine deliberately holds no coordinates, so a stop it emits knows
 * only which leg it is on and how far along: "En route to Stikine Region,
 * British Columbia (524 mi)". That sentence names the *destination*, which for
 * a rest outside Nashville is three thousand miles wrong.
 *
 * The coordinates do exist -- they are interpolated onto the route polyline at
 * serialization -- so the name is one reverse lookup away. Doing that while
 * planning would add a second per stop to a request the driver is already
 * waiting on, and a long trip has sixteen of them. So it happens here instead,
 * after the timeline is on screen, filling the rows in as answers arrive.
 */

import { reverseGeocode } from "./api";

/** Nominatim's usage policy is one request per second, and it is not
 *  negotiable: exceeding it gets an application blocked, not throttled. */
const MIN_GAP_MS = 1100;

/** ~1 km. Two stops this close share an answer, which costs nothing and is
 *  usually the same town anyway. */
const KEY_PRECISION = 2;

const key = (lat: number, lon: number) =>
  `${lat.toFixed(KEY_PRECISION)},${lon.toFixed(KEY_PRECISION)}`;

const resolved = new Map<string, string>();
const pending = new Map<string, Promise<string>>();

let chain: Promise<unknown> = Promise.resolve();

/** True for a label the engine had to invent because it knew no place name. */
export function isEnRouteLabel(label: string): boolean {
  return label.startsWith("En route");
}

export const cachedPlace = (lat: number, lon: number): string | undefined =>
  resolved.get(key(lat, lon));

/**
 * The place at a coordinate, looked up at most once and never faster than
 * Nominatim allows.
 *
 * Requests are chained rather than fired together: sixteen parallel lookups
 * would breach the rate limit on the first row and get nothing for the rest.
 */
export function placeAt(lat: number, lon: number): Promise<string> {
  const id = key(lat, lon);

  const known = resolved.get(id);
  if (known !== undefined) return Promise.resolve(known);

  const inFlight = pending.get(id);
  if (inFlight) return inFlight;

  const request = chain
    .then(() => new Promise((resume) => setTimeout(resume, MIN_GAP_MS)))
    .then(() => reverseGeocode(lat, lon))
    .then((place) => {
      // The API degrades to a coordinate string when it cannot name a point.
      // That is no better than what the row already shows, so it is discarded
      // rather than cached -- leaving the engine's own description in place.
      const label = /^-?\d/.test(place.label) ? "" : place.label;
      if (label) resolved.set(id, label);
      return label;
    })
    .catch(() => "");

  pending.set(id, request);
  chain = request;
  return request;
}
