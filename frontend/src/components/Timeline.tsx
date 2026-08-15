import {
  CYCLE_LIMIT,
  cycleTier,
  STOP_LABEL,
  type ForcedStop,
  type StopKind,
  type TimelineEntry,
} from "../types";
import { TRUCK_STOP_LABEL } from "../truckStops";

const KIND_COLOR: Record<string, string> = {
  driving: "var(--driving)",
  pickup: "var(--on-duty)",
  dropoff: "var(--on-duty)",
  fuel: "var(--on-duty)",
  break: "var(--off-duty)",
  reset: "var(--sleeper-berth)",
  restart: "var(--sleeper-berth)",
  off_duty: "var(--off-duty)",
  sleeper_berth: "var(--sleeper-berth)",
  on_duty: "var(--on-duty)",
};

/** Plain-language reason each stop exists, tied back to the rule that forces it. */
const KIND_REASON: Partial<Record<StopKind, string>> = {
  pickup: "Loading is on-duty time under § 395.2",
  dropoff: "Unloading is on-duty time under § 395.2",
  fuel: "Fueling every 1,000 miles, logged on duty",
  break: "30 consecutive minutes after 8 cumulative driving hours",
  reset: "10 consecutive hours restores the 11- and 14-hour clocks",
  restart: "34 consecutive hours returns the 70-hour cycle to zero",
};

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function day(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

function duration(hours: number): string {
  const whole = Math.floor(hours);
  const minutes = Math.round((hours - whole) * 60);
  if (whole === 0) return `${minutes}m`;
  return minutes === 0 ? `${whole}h` : `${whole}h ${minutes}m`;
}

/**
 * Miles, driving hours and 70-hour cycle accumulated through each row.
 *
 * Precomputed in one pass instead of a running total inside the render, so the
 * figures don't depend on the rows being drawn in order. Stops carry the total
 * as it stood when the driver pulled in, which is what makes "466 mi so far"
 * line up with the break that the 466th mile forced.
 *
 * Cycle time follows § 395.2's definition, not the driving clock: every on-duty
 * minute counts, so loading, unloading and fuelling all burn it while the miles
 * and driving hours stand still. A 34-hour restart zeroes it outright. The
 * arithmetic deliberately mirrors `_cycle_after_last_restart` in the backend
 * serializer, so the last row lands on the same figure the summary meter shows.
 */
function runningTotals(timeline: TimelineEntry[], cycleAtStart: number) {
  let miles = 0;
  let hours = 0;
  let cycle = cycleAtStart;
  return timeline.map((entry) => {
    const driving = entry.kind === "driving";
    if (driving) {
      miles += entry.miles;
      hours += entry.duration_hours;
    }
    if (entry.kind === "restart") {
      cycle = 0;
    } else if (driving || entry.status === "on_duty") {
      cycle += entry.duration_hours;
    }
    // Rows where the cycle stood still -- a break, a 10-hour rest -- say so by
    // omitting it rather than repeating an unchanged number.
    const cycleMoved = driving || entry.status === "on_duty" || entry.kind === "restart";
    return { miles, hours, cycle, cycleMoved };
  });
}

interface Props {
  timeline: TimelineEntry[];
  /** Hours already on the 70-hour cycle before this trip's first mile. */
  cycleUsedAtStart: number;
  /** Move a stop to a chosen facility and re-plan the rest of the trip. */
  onShift?: (stop: ForcedStop) => void;
  /** A re-plan is in flight; the controls are inert until it lands. */
  shifting?: boolean;
}

export function Timeline({
  timeline,
  cycleUsedAtStart,
  onShift,
  shifting = false,
}: Props) {
  const stopCount = timeline.filter((entry) => entry.kind !== "driving").length;
  const totals = runningTotals(timeline, cycleUsedAtStart);
  const tripMiles = totals.length ? totals[totals.length - 1].miles : 0;

  return (
    <div className="card">
      <div className="card__head">
        <span className="card__title">Trip timeline</span>
        <span className="card__hint">
          {stopCount} stop{stopCount === 1 ? "" : "s"} &middot;{" "}
          {timeline.length - stopCount} driving leg
          {timeline.length - stopCount === 1 ? "" : "s"}
        </span>
      </div>

      {/* Scrolls within itself rather than lengthening the page: the list runs
          to a few dozen rows on a long trip, and the log sheets below it are
          the thing the driver actually came for. */}
      <div className="card__body timeline__scroll">
        <ol className="stops">
          {timeline.map((entry, index) => {
            const driving = entry.kind === "driving";
            return (
              <li
                className={driving ? "stop stop--driving" : "stop"}
                key={index}
              >
                <span
                  className="stop__rail"
                  style={{ color: KIND_COLOR[entry.kind] ?? "var(--off-duty)" }}
                >
                  <span className={driving ? "stop__line" : "stop__dot"} />
                </span>

                <div className="stop__body">
                  {/* Name and duration share a line, time sits under it, and
                      the place gets the full width. A right-hand meta column
                      left too little room for a place name in the narrow rail
                      and broke them across four lines. */}
                  <div className="stop__top">
                    {/* The short name, not the engine's note: the note carries
                        its reason in parentheses, which is printed below and
                        would otherwise be truncated away here anyway. Driving
                        legs keep their own label -- it holds the mileage. */}
                    <span className="stop__label">
                      {STOP_LABEL[entry.kind] ?? entry.label}
                    </span>
                    <span className="stop__dur num">
                      {duration(entry.duration_hours)}
                    </span>
                  </div>

                  {/* Running totals through the end of this row. Each row shows
                      its own figure on the line above; without the cumulative
                      one beside it there is no way to tell whether "Drive 99
                      mi" is the first hour of the trip or the last.

                      Distance and driving hours are on driving rows only -- a
                      stop covers no ground, so repeating them under every break
                      would be noise. The cycle is the exception: it keeps
                      running through pickup, drop-off and fuelling, which is
                      exactly the part drivers get wrong, so it prints wherever
                      it actually moved. */}
                  {driving && (
                    <div className="stop__running num">
                      {Math.round(totals[index].miles).toLocaleString()} of{" "}
                      {Math.round(tripMiles).toLocaleString()} mi &middot;{" "}
                      {duration(totals[index].hours)} driven
                    </div>
                  )}

                  {/* Its own line rather than appended to the one above: the
                      two together run past this card's width beside the map and
                      wrapped mid-phrase. Broken deliberately, the cycle figure
                      also lines up down the list where it can be compared. */}
                  {totals[index].cycleMoved && (
                    <div className="stop__running num">
                      {entry.kind === "restart" ? "cycle reset to " : "cycle "}
                      <strong className={`cycle--${cycleTier(totals[index].cycle)}`}>
                        {totals[index].cycle.toFixed(2)}
                      </strong>
                      /{CYCLE_LIMIT}
                    </div>
                  )}

                  <div className="stop__when num">
                    {day(entry.start)} · {clock(entry.start)}
                  </div>

                  {/* No preposition. A driving event's location is where the
                      leg *starts*, not where it ends, so "toward" was wrong --
                      and on a mid-route leg it read "toward En route to X".
                      Truncated rather than wrapped so rows stay scannable;
                      the full text is on the title. */}
                  <div className="stop__loc" title={entry.location}>
                    {entry.location}
                  </div>

                  {KIND_REASON[entry.kind] && (
                    <div className="stop__why">{KIND_REASON[entry.kind]}</div>
                  )}
                  {entry.satisfies_break && (
                    <span className="tag">Also satisfies the 30-min break</span>
                  )}
                  {/* Otherwise a stop the driver moved looks exactly like one
                      the regulation forced, and the change they just made is
                      invisible in the very list they made it from. */}
                  {entry.moved_by_driver && (
                    <span className="tag tag--moved">You moved this stop</span>
                  )}

                  {/* The engine picks the mile marker; it cannot pick a place.
                      Every one of these sits at or before that marker, because
                      starting a rest early is legal and finishing the drive
                      late is not. */}
                  {entry.facilities.length > 0 && (
                    <div className="parking">
                      <div className="parking__head">
                        Where to stop &mdash; {entry.facilities.length} within{" "}
                        {Math.ceil(
                          entry.facilities[entry.facilities.length - 1]
                            .miles_before_stop,
                        )}{" "}
                        mi before
                      </div>
                      <ul className="parking__list">
                        {entry.facilities.map((facility) => (
                          <li className="parking__item" key={facility.osm_id}>
                            {/* The whole row is the control. Picking a place to
                                sleep and re-planning around it are the same
                                decision, so they should not be two clicks. */}
                            <button
                              type="button"
                              className="parking__pick"
                              disabled={shifting}
                              onClick={() =>
                                onShift?.({
                                  route_miles: facility.route_miles,
                                  kind: entry.kind as ForcedStop["kind"],
                                })
                              }
                              title={`Move this stop to ${facility.name} (${TRUCK_STOP_LABEL[facility.kind]}) and re-plan the rest of the trip`}
                            >
                              <span className="parking__name">
                                {facility.name}
                              </span>
                              {/* The number being traded: stop here and the
                                  whole remaining plan shifts earlier by this
                                  much. */}
                              <span className="parking__when num">
                                {facility.miles_before_stop < 0.5
                                  ? "at the stop"
                                  : `${Math.round(facility.miles_before_stop)} mi earlier`}
                              </span>
                              {facility.amenities.length > 0 && (
                                <span className="parking__amenities">
                                  {facility.amenities.join(" · ")}
                                </span>
                              )}
                            </button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
}
