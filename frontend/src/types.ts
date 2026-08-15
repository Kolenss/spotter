import type { TruckStopKind } from "./truckStops";

export type DutyStatus = "off_duty" | "sleeper_berth" | "driving" | "on_duty";

export type StopKind =
  | "driving"
  | "pickup"
  | "dropoff"
  | "fuel"
  | "break"
  | "reset"
  | "restart"
  | DutyStatus;

export interface TripInputs {
  current_location: string;
  pickup_location: string;
  dropoff_location: string;
  current_cycle_used: number;
  start_time: string;
}

/** One row in the history list. The full plan is fetched only when opened. */
export interface TripListItem {
  id: number;
  created_at: string;
  current_label: string;
  pickup_label: string;
  dropoff_label: string;
  total_distance_miles: number;
}

export interface Waypoint {
  kind: "start" | "pickup" | "dropoff";
  label: string;
  lat: number;
  lon: number;
}

export interface RouteInfo {
  total_miles: number;
  total_drive_hours: number;
  current_label: string;
  pickup_label: string;
  dropoff_label: string;
  /** True whenever mileage is a straight-line estimate rather than a road route. */
  distances_estimated: boolean;
  /** True when no drivable road path exists between the waypoints, as opposed
   *  to the routing service simply being down. Retrying will not help. */
  no_road_route: boolean;
  /** Simplified road geometry as [lat, lon] pairs, ready for Leaflet. */
  path: [number, number][];
  waypoints: Waypoint[];
}

export interface TripSummary {
  total_days: number;
  total_driving_hours: number;
  total_on_duty_hours: number;
  trip_duration_hours: number;
  cycle_used_at_start: number;
  cycle_used_at_end: number;
  fuel_stops: number;
  required_breaks: number;
  required_rests: number;
  restarts: number;
  starts_at: string | null;
  ends_at: string | null;
}

/**
 * Somewhere the driver could legally put the truck, near a forced stop.
 *
 * Reuses `TruckStopKind` and `TRUCK_STOP_LABEL` from the map layer rather than
 * declaring a second set of names for the same four things — the anti-drift
 * rule the duty-status colours and STOP_LABEL already follow. The two features
 * reach Overpass by different routes (this one server-side and persisted with
 * the trip, the map layer live from the browser) but they are describing the
 * same places and must call them the same thing.
 */
export interface Facility {
  osm_id: string;
  kind: TruckStopKind;
  name: string;
  lat: number;
  lon: number;
  /** Position along the route, in the same road miles the engine counts in. */
  route_miles: number;
  /** How far off the route it sits. */
  detour_miles: number;
  /** How much earlier than planned stopping here would be. Never negative. */
  miles_before_stop: number;
  amenities: string[];
}

/** One entry in the trip timeline — a driving leg or a stop. */
export interface TimelineEntry {
  kind: StopKind;
  status: DutyStatus;
  label: string;
  location: string;
  start: string;
  end: string;
  duration_hours: number;
  miles: number;
  /** Interpolated position along the route; null if geometry was unavailable. */
  lat: number | null;
  lon: number | null;
  /** A 30+ minute non-driving block also satisfies the required break. */
  satisfies_break: boolean;
  /** Places to park, at or before this stop. Empty for driving legs, for the
   *  shipper and receiver, and whenever Overpass had nothing or was down. */
  facilities: Facility[];
}

export interface LogSegment {
  status: DutyStatus;
  /** Hours from midnight, 0-24. */
  start_hour: number;
  end_hour: number;
  location: string;
  note: string;
  miles: number;
}

export interface LogRemark {
  hour: number;
  location: string;
  note: string;
}

export interface DailyLog {
  date: string;
  total_miles: number;
  totals: Record<DutyStatus, number>;
  /** Always 24. Printed on the sheet as the sum of the totals column. */
  total_hours: number;
  segments: LogSegment[];
  remarks: LogRemark[];
}

export interface Trip {
  id: number;
  created_at: string;
  inputs: TripInputs;
  route: RouteInfo;
  summary: TripSummary;
  timeline: TimelineEntry[];
  daily_logs: DailyLog[];
}

/** The three locations, as keyed in the request and on the picker map. */
export type LocationKey =
  | "current_location"
  | "pickup_location"
  | "dropoff_location";

export interface TripRequest {
  current_location: string;
  pickup_location: string;
  dropoff_location: string;
  current_cycle_used: number;
  start_time?: string;
  /* Present only when the driver picked a search result or pinned the map.
     The backend then uses that exact point instead of geocoding the text. */
  current_lat?: number | null;
  current_lon?: number | null;
  pickup_lat?: number | null;
  pickup_lon?: number | null;
  dropoff_lat?: number | null;
  dropoff_lon?: number | null;
}

/** One row of the location field's suggestions dropdown. */
export interface PlaceSuggestion {
  label: string;
  lat: number;
  lon: number;
}

/**
 * Short name for each kind of stop, shared by the map legend, the map tooltips
 * and the trip timeline so a stop is called the same thing wherever you meet
 * it — the same rule the duty-status colours follow.
 *
 * Deliberately shorter than the engine's own note, which reads "30-minute break
 * (8 cumulative driving hours)". The parenthetical is the *reason*, and the
 * timeline prints that on its own line underneath.
 */
export const STOP_LABEL: Partial<Record<StopKind | "start", string>> = {
  start: "Start",
  pickup: "Pickup",
  dropoff: "Drop-off",
  fuel: "Fuel stop",
  break: "30-min break",
  reset: "10-hour rest",
  restart: "34-hour restart",
};

/** Hours of on-duty time allowed in the rolling 8-day cycle, § 395.3(b)(2). */
export const CYCLE_LIMIT = 70;

export type CycleTier = "ok" | "warn" | "high" | "full";

/**
 * How much of the 70-hour cycle is gone, as a four-step severity: green while
 * there is a comfortable margin, yellow past halfway, orange once a full shift
 * no longer fits, red when the driver is effectively out.
 *
 * One definition, used by both the summary meter and the timeline, so the
 * colours can't say different things in the two places — the same anti-drift
 * rule the duty-status colours and STOP_LABEL follow. The colours themselves
 * are `--cycle-*` in index.css and the classes are `.cycle--<tier>` /
 * `.meter__fill--<tier>`.
 *
 * The 75% boundary is not arbitrary: 52.5 of 70 hours leaves 17.5, which is
 * still more than a maximum 14-hour shift, so orange begins exactly where the
 * remaining cycle stops covering one more full day of work.
 */
export function cycleTier(usedHours: number): CycleTier {
  const ratio = usedHours / CYCLE_LIMIT;
  if (ratio >= 0.9) return "full";
  if (ratio >= 0.75) return "high";
  if (ratio >= 0.5) return "warn";
  return "ok";
}

export const DUTY_ROWS: { status: DutyStatus; label: string; short: string }[] = [
  { status: "off_duty", label: "Off Duty", short: "OFF" },
  { status: "sleeper_berth", label: "Sleeper Berth", short: "SB" },
  { status: "driving", label: "Driving", short: "D" },
  { status: "on_duty", label: "On Duty (Not Driving)", short: "ON" },
];
