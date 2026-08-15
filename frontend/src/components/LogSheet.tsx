import type { DailyLog, Trip } from "../types";
import { LogSheetGrid } from "./LogSheetGrid";

function formatDate(iso: string): string {
  // Parsed as a local date deliberately: a log sheet is kept in the home
  // terminal's time zone, so it must not shift when read elsewhere.
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

/**
 * Driving hours per shift within this calendar day.
 *
 * The 11-hour limit applies to a driving *window*, not to a calendar day, and
 * a window freely straddles midnight. So a single sheet can legitimately show
 * more than 11 hours of driving when it holds the tail of one shift and the
 * start of the next. Splitting the total by shift makes that legible instead
 * of alarming.
 */
function drivingByShift(log: DailyLog): number[] {
  const shifts: number[] = [];
  let current = 0;

  for (const segment of log.segments) {
    const length = segment.end_hour - segment.start_hour;
    const isReset =
      length >= 10 &&
      (segment.status === "off_duty" || segment.status === "sleeper_berth");

    if (isReset) {
      if (current > 0) shifts.push(current);
      current = 0;
    } else if (segment.status === "driving") {
      current += length;
    }
  }
  if (current > 0) shifts.push(current);
  return shifts;
}

interface Props {
  log: DailyLog;
  trip: Trip;
  index: number;
  total: number;
}

export function LogSheet({ log, trip, index, total }: Props) {
  const balanced = Math.abs(log.total_hours - 24) < 0.005;
  const shifts = drivingByShift(log);
  const multiShift = shifts.length > 1;

  return (
    <article className="sheet">
      <header className="sheet__head">
        <div>
          <div className="sheet__title">
            Driver&rsquo;s Daily Log &middot; One Calendar Day &mdash; 24 Hours
          </div>
          <div className="sheet__date">{formatDate(log.date)}</div>
        </div>

        <dl className="sheet__fields">
          <div className="sheet__field">
            <dt>Sheet</dt>
            <dd className="num">
              {index + 1} of {total}
            </dd>
          </div>
          <div className="sheet__field">
            <dt>Total Miles Driving</dt>
            <dd className="num">{log.total_miles.toLocaleString()}</dd>
          </div>
          <div className="sheet__field">
            <dt>From</dt>
            <dd>{trip.route.current_label}</dd>
          </div>
          <div className="sheet__field">
            <dt>To</dt>
            <dd>{trip.route.dropoff_label}</dd>
          </div>
        </dl>
      </header>

      <div className="sheet__gridwrap">
        <LogSheetGrid log={log} />
      </div>

      <footer className="sheet__foot">
        <span className="certify">
          {multiShift
            ? `${log.totals.driving} driving hrs across ${shifts.length} shifts on this date — ` +
              `${shifts.map((hours) => hours.toFixed(2)).join(" + ")}, each within the 11-hour limit.`
            : "I certify that these entries are true and correct."}
        </span>
        <span className="total-check">
          Totals&nbsp;
          <span className={balanced ? "total-check__ok" : "total-check__bad"}>
            {balanced
              ? `${log.total_hours.toFixed(2)} hrs ✓`
              : `${log.total_hours.toFixed(2)} hrs ✗ must equal 24`}
          </span>
        </span>
      </footer>
    </article>
  );
}
