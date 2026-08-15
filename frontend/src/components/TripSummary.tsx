import { CYCLE_LIMIT, cycleTier, type Trip } from "../types";

interface Props {
  trip: Trip;
}

export function TripSummary({ trip }: Props) {
  const { summary, route } = trip;
  const remaining = Math.max(0, CYCLE_LIMIT - summary.cycle_used_at_end);
  const tier = cycleTier(summary.cycle_used_at_end);

  return (
    <div className="stats">
      <div className="stat">
        <div className="stat__label">Total distance</div>
        <div className="stat__value num">
          {route.total_miles.toLocaleString()}
          <span className="stat__unit">mi</span>
        </div>
        <div className="stat__foot">
          {route.no_road_route
            ? "Straight line — no road route"
            : route.distances_estimated
              ? "Estimated"
              : "Road route"}
        </div>
      </div>

      <div className="stat">
        <div className="stat__label">Driving time</div>
        <div className="stat__value num">
          {summary.total_driving_hours}
          <span className="stat__unit">hrs</span>
        </div>
        {/* Says "across N days" because a three-figure number here reads as a
            daily or per-shift figure otherwise, and 116 of either is illegal. */}
        <div className="stat__foot">
          Across all {summary.total_days} day
          {summary.total_days === 1 ? "" : "s"} &middot; max 11 per shift
        </div>
      </div>

      <div className="stat">
        <div className="stat__label">Trip length</div>
        <div className="stat__value num">
          {summary.total_days}
          <span className="stat__unit">
            {summary.total_days === 1 ? "day" : "days"}
          </span>
        </div>
        <div className="stat__foot">
          {summary.total_days} log{summary.total_days === 1 ? "" : "s"} to file
        </div>
      </div>

      <div className="stat">
        <div className="stat__label">Required stops</div>
        <div className="stat__value num">
          {summary.fuel_stops + summary.required_breaks + summary.required_rests +
            summary.restarts}
        </div>
        <div className="stat__foot">
          {summary.required_rests} rest{summary.required_rests === 1 ? "" : "s"} ·{" "}
          {summary.fuel_stops} fuel · {summary.required_breaks} break
          {summary.required_breaks === 1 ? "" : "s"}
          {summary.restarts > 0 && ` · ${summary.restarts} restart`}
        </div>
      </div>

      <div className="stat">
        <div className="stat__label">70-hour cycle</div>
        <div className="stat__value num">
          {/* Only the used figure takes the tier colour; the "/ 70" stays
              neutral so the colour reads as severity, not as decoration. */}
          <span className={`cycle--${tier}`}>{summary.cycle_used_at_end}</span>
          <span className="stat__unit">/ {CYCLE_LIMIT}</span>
        </div>
        <div className="meter">
          <div
            className={`meter__fill meter__fill--${tier}`}
            style={{
              width: `${Math.min(100, (summary.cycle_used_at_end / CYCLE_LIMIT) * 100)}%`,
            }}
          />
        </div>
        <div className="stat__foot">
          {remaining.toFixed(2)} hrs left at drop-off
        </div>
      </div>
    </div>
  );
}
