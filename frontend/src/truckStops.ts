/**
 * Truck stops from OpenStreetMap, fetched straight from the browser.
 *
 * Unlike Nominatim, Overpass sets `Access-Control-Allow-Origin: *` and does not
 * require a User-Agent, so this needs no Django proxy -- and must not have one.
 * Overpass allows only two concurrent queries per IP address; behind a proxy
 * every user in the world would share the server's two slots and starve each
 * other out. Called from the browser, each driver spends their own quota.
 */

export type TruckStopKind = "truck_stop" | "fuel" | "parking" | "rest_area";

export const TRUCK_STOP_LABEL: Record<TruckStopKind, string> = {
  truck_stop: "Truck stop",
  fuel: "Truck fuel",
  parking: "Truck parking",
  rest_area: "Rest area",
};

export interface TruckStop {
  id: string;
  kind: TruckStopKind;
  /** The OSM name where there is one, otherwise the kind. */
  label: string;
  named: boolean;
  lat: number;
  lon: number;
}

export interface BBox {
  south: number;
  west: number;
  north: number;
  east: number;
}

const ENDPOINT = "https://overpass-api.de/api/interpreter";

/**
 * Below this the query stops being worth waiting for. Measured over Texas:
 *
 *   ~35 mi across (z11)    44 stops    3s
 *   ~200 mi across (z8)   182 stops   10s
 *   ~500 mi across (z6)   895 stops   30s
 *
 * z8 covers a metro area and its surrounding interstates, which is as wide as
 * a driver plausibly picks from. z6 costs three times the wait for a screen of
 * dots too dense to click, and holds one of the two rate-limit slots while it
 * runs.
 */
export const MIN_ZOOM = 8;

/** Past this many, only named stops are drawn -- see NAMED_ONLY_ABOVE use. */
export const NAMED_ONLY_ABOVE = 120;

/* Snap the request box to a grid so nudging the map by a few pixels reuses the
   previous answer instead of spending one of the two slots on it. Padding out
   to the grid also means the data already covers a short pan in any direction. */
const GRID_DEGREES = 0.25;

/**
 * Deliberately excludes warehouses and depots.
 *
 * `building=warehouse` swamps everything -- 711 of 800 results over Dallas, all
 * but 42 unnamed -- and buries the actual truck stops. `industrial=depot`
 * returns municipal bus and rail yards, which are not truck facilities. What is
 * left is the set a driver would recognise: Love's, Pilot, Flying J, Petro.
 */
function overpassQuery(box: BBox): string {
  const b = `${box.south},${box.west},${box.north},${box.east}`;
  const clauses = [
    `node["amenity"="truck_stop"](${b});`,
    `way["amenity"="truck_stop"](${b});`,
    `node["amenity"="fuel"]["hgv"="yes"](${b});`,
    `way["amenity"="fuel"]["hgv"="yes"](${b});`,
    `node["amenity"="parking"]["hgv"="yes"](${b});`,
    `way["amenity"="parking"]["hgv"="yes"](${b});`,
    `node["highway"="rest_area"](${b});`,
    `way["highway"="services"](${b});`,
  ].join("");
  // `out center` because ways are polygons -- this returns one point each.
  return `[out:json][timeout:25];(${clauses});out center 250;`;
}

/** Longitude back into [-180, 180]. Leaflet keeps counting past the
 *  antimeridian -- drag west from California and it reports -207, which is a
 *  real place (153°E) but not a coordinate Overpass accepts. It answers those
 *  with a 400. */
const wrapLon = (lon: number) => (((((lon + 180) % 360) + 360) % 360)) - 180;

const clampLat = (lat: number) => Math.max(-90, Math.min(90, lat));

/**
 * Snap a viewport to the request grid, or null if it cannot be queried.
 *
 * Null means the viewport straddles the antimeridian, where the box would run
 * from +170 to -170 and describe the entire globe the wrong way round. Overpass
 * has no way to express that; splitting it into two queries would spend both
 * rate-limit slots to cover the middle of the Pacific.
 */
export function snapBox(box: BBox): BBox | null {
  const down = (value: number) => Math.floor(value / GRID_DEGREES) * GRID_DEGREES;
  const up = (value: number) => Math.ceil(value / GRID_DEGREES) * GRID_DEGREES;

  const west = wrapLon(box.west);
  const east = wrapLon(box.east);
  if (west >= east) return null;

  return {
    south: down(clampLat(box.south)),
    west: down(west),
    north: up(clampLat(box.north)),
    east: up(east),
  };
}

export function boxKey(box: BBox): string {
  return [box.south, box.west, box.north, box.east]
    .map((value) => value.toFixed(2))
    .join(",");
}

interface OverpassElement {
  type: string;
  id: number;
  lat?: number;
  lon?: number;
  center?: { lat: number; lon: number };
  tags?: Record<string, string>;
}

function classify(tags: Record<string, string>): TruckStopKind | null {
  if (tags.amenity === "truck_stop") return "truck_stop";
  if (tags.amenity === "fuel") return "fuel";
  if (tags.amenity === "parking") return "parking";
  if (tags.highway === "rest_area" || tags.highway === "services") return "rest_area";
  return null;
}

function toStop(element: OverpassElement): TruckStop | null {
  const tags = element.tags ?? {};
  const kind = classify(tags);
  if (!kind) return null;

  // Nodes carry lat/lon directly; ways come back with a computed centre.
  const lat = element.lat ?? element.center?.lat;
  const lon = element.lon ?? element.center?.lon;
  if (lat === undefined || lon === undefined) return null;

  const name = tags.name?.trim();
  return {
    id: `${element.type}/${element.id}`,
    kind,
    label: name || TRUCK_STOP_LABEL[kind],
    named: Boolean(name),
    lat,
    lon,
  };
}

const cache = new Map<string, TruckStop[]>();

export class OverpassBusy extends Error {}

/**
 * Truck stops within a snapped box. Cached, so panning back is free.
 *
 * Throws `OverpassBusy` when both query slots are taken -- the server answers
 * that with an HTML error page rather than JSON, and the caller should say so
 * rather than show an empty map, which would read as "no truck stops here".
 */
export async function fetchTruckStops(
  box: BBox,
  signal: AbortSignal,
): Promise<TruckStop[]> {
  const key = boxKey(box);
  const hit = cache.get(key);
  if (hit) return hit;

  const response = await fetch(ENDPOINT, {
    method: "POST",
    body: new URLSearchParams({ data: overpassQuery(box) }),
    signal,
  });

  if (!response.ok) throw new OverpassBusy(`Overpass returned ${response.status}`);

  const text = await response.text();
  let payload: { elements?: OverpassElement[] };
  try {
    payload = JSON.parse(text);
  } catch {
    // Rate-limited replies are an HTML page, not JSON.
    throw new OverpassBusy("Overpass is busy");
  }

  const stops = (payload.elements ?? [])
    .map(toStop)
    .filter((stop): stop is TruckStop => stop !== null);

  // Named stops first: those are the ones a driver is looking for, and they
  // should win when markers overlap.
  stops.sort((a, b) => Number(b.named) - Number(a.named));

  cache.set(key, stops);
  return stops;
}
