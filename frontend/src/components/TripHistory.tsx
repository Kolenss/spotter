import type { TripListItem } from "../types";

/** Heading for a group of trips. Timestamps are naive home-terminal local
 *  time, which is what `new Date` assumes for an ISO string without a zone. */
function dayHeading(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Trips arrive newest first, so walking them in order keeps the groups sorted
 *  without a second pass. Keyed on the calendar date, not the full timestamp. */
function groupByDay(trips: TripListItem[]): [string, TripListItem[]][] {
  const groups = new Map<string, TripListItem[]>();
  for (const trip of trips) {
    const key = trip.created_at.slice(0, 10);
    const existing = groups.get(key);
    if (existing) existing.push(trip);
    else groups.set(key, [trip]);
  }
  return [...groups.entries()];
}

interface Props {
  trips: TripListItem[];
  /** The trip currently shown, so the list can mark where you are. */
  activeId: number | null;
  onOpen: (id: number) => void;
}

export function TripHistory({ trips, activeId, onOpen }: Props) {
  const groups = groupByDay(trips);

  return (
    <div className="card">
      <div className="card__head">
        <span className="card__title">History</span>
        {trips.length > 0 && (
          <span className="card__hint">
            {trips.length} trip{trips.length === 1 ? "" : "s"}
          </span>
        )}
      </div>
      <div className="card__body history">
        {groups.length === 0 ? (
          <p className="history__empty">
            Trips you plan are saved here so you can reopen their log sheets.
          </p>
        ) : (
          groups.map(([key, dayTrips]) => (
            <section className="history__day" key={key}>
              <h3 className="history__date">{dayHeading(dayTrips[0].created_at)}</h3>
              <ul className="history__list">
                {dayTrips.map((trip) => (
                  <li key={trip.id}>
                    <button
                      type="button"
                      className="history__item"
                      aria-current={trip.id === activeId ? "true" : undefined}
                      onClick={() => onOpen(trip.id)}
                    >
                      <span className="history__route">
                        {trip.pickup_label} &rarr; {trip.dropoff_label}
                      </span>
                      <span className="history__meta">
                        <span className="num">{clock(trip.created_at)}</span>
                        <span aria-hidden="true">&middot;</span>
                        <span className="num">
                          {Math.round(trip.total_distance_miles).toLocaleString()} mi
                        </span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ))
        )}
      </div>
    </div>
  );
}
