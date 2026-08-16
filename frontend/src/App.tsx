import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  listTrips,
  loadHistory,
  loadTrip,
  planTrip,
  type FieldErrors,
} from "./api";
import { LogSheet } from "./components/LogSheet";
import { PickerMap, type PickedPlaces } from "./components/PickerMap";
import { Timeline } from "./components/Timeline";
import { TripForm } from "./components/TripForm";
import { TripHistory } from "./components/TripHistory";
import { TripMap } from "./components/TripMap";
import { TripSummary } from "./components/TripSummary";
import {
  DUTY_ROWS,
  type LocationKey,
  type PlaceSuggestion,
  type Trip,
  type TripListItem,
  type TripRequest,
} from "./types";

const STATUS_COLOR: Record<string, string> = {
  off_duty: "var(--off-duty)",
  sleeper_berth: "var(--sleeper-berth)",
  driving: "var(--driving)",
  on_duty: "var(--on-duty)",
};

type Tab = "plan" | "history";

export default function App() {
  const [trip, setTrip] = useState<Trip | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [history, setHistory] = useState<TripListItem[]>([]);
  const [tab, setTab] = useState<Tab>("plan");

  /* The one request that has to survive a cold start: it fires on first paint,
     which is exactly when a free-tier service is most likely to be asleep. */
  const [historyWaking, setHistoryWaking] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);

  const refreshHistory = useCallback(() => {
    setHistoryError(null);
    loadHistory(() => setHistoryWaking(true))
      .then((trips) => setHistory(trips))
      .catch((caught) =>
        setHistoryError(
          caught instanceof ApiError
            ? caught.message
            : "Previous trips could not be loaded.",
        ),
      )
      .finally(() => setHistoryWaking(false));
  }, []);

  useEffect(refreshHistory, [refreshHistory]);

  /* Owned here rather than in either child: the form and the picker map sit in
     different columns, and both read and write these. */
  const [picked, setPicked] = useState<PickedPlaces>({});
  const [activeField, setActiveField] = useState<LocationKey>("current_location");

  function handlePicked(key: LocationKey, place: PlaceSuggestion | null) {
    setPicked((current) => {
      if (place) return { ...current, [key]: place };
      const { [key]: _dropped, ...rest } = current;
      return rest;
    });
  }

  async function handleSubmit(request: TripRequest) {
    setLoading(true);
    setError(null);
    setFieldErrors({});

    try {
      setTrip(await planTrip(request));
      // The plan is already on screen; refreshing the list is cosmetic, so it
      // must not delay it or fail the submit.
      listTrips().then(setHistory);
    } catch (caught) {
      const failure =
        caught instanceof ApiError
          ? caught
          : new ApiError("Something went wrong while planning the trip.");
      setError(failure.message);
      setFieldErrors(failure.fieldErrors);
      setTrip(null);
    } finally {
      setLoading(false);
    }
  }

  /** Clears the plan and returns to the form, leaving the inputs in place so a
   *  second run can change one field rather than retype all four. */
  function handleBack() {
    setTrip(null);
    setError(null);
    setTab("plan");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  /**
   * Selecting a tab, where "Plan a trip" means *plan a trip* and not merely
   * "show me the form".
   *
   * The results column follows `trip`, not the tab, so opening something from
   * History and then coming back here used to leave that trip on screen with
   * the picker map unmounted underneath it -- the form was right there and the
   * map could not be clicked. Switching to this tab therefore puts the plan
   * away, exactly as the back link does. The trip is not lost; it is in
   * History, which is where it was opened from.
   */
  function handleTab(next: Tab) {
    if (next === "plan" && trip) {
      setTrip(null);
      setError(null);
    }
    setTab(next);
  }

  async function handleOpenTrip(id: number) {
    setLoading(true);
    setError(null);
    setFieldErrors({});

    try {
      setTrip(await loadTrip(id));
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Something went wrong while opening the trip.",
      );
      setTrip(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <header className="masthead">
        <div className="shell masthead__bar">
          <div className="masthead__brand">
            <h1>Spotter</h1>
            <span className="masthead__sub">
              HOS trip planner &amp; electronic log generator
            </span>
          </div>
          {/* The citation stays first -- to a reader who knows it, it says in
              four tokens which rulebook this was built against -- and three
              words gloss it for one who doesn't. The driver class is left to
              the form's own hint rather than repeated here. */}
          <div className="masthead__reg">
            <strong>49 CFR Part 395</strong> &middot; FMCSA hours-of-service rules
          </div>
        </div>
      </header>

      <main className="shell page">
        <div className="layout">
          <div className="layout__side">
            <div className="tabs" role="tablist" aria-label="Trip panel">
              <button
                type="button"
                role="tab"
                id="tab-plan"
                className="tab"
                aria-selected={tab === "plan"}
                aria-controls="panel-plan"
                onClick={() => handleTab("plan")}
              >
                Plan a trip
              </button>
              <button
                type="button"
                role="tab"
                id="tab-history"
                className="tab"
                aria-selected={tab === "history"}
                aria-controls="panel-history"
                onClick={() => handleTab("history")}
              >
                History
                {history.length > 0 && (
                  <span className="tab__count num">{history.length}</span>
                )}
              </button>
            </div>

            {/* Both panels stay mounted and the inactive one is hidden. The
                form holds its own field state, so unmounting it to switch to
                History would silently empty a half-filled form. */}
            <div
              role="tabpanel"
              id="panel-plan"
              aria-labelledby="tab-plan"
              hidden={tab !== "plan"}
            >
              <TripForm
                onSubmit={handleSubmit}
                loading={loading}
                fieldErrors={fieldErrors}
                picked={picked}
                onPicked={handlePicked}
                active={activeField}
                onActiveChange={setActiveField}
              />

              {error && (
                <div className="alert" role="alert" style={{ marginTop: 16 }}>
                  {error}
                </div>
              )}

              <div className="card" style={{ marginTop: 16 }}>
                <div className="card__head">
                  <span className="card__title">Assumptions</span>
                </div>
                <div className="card__body assumptions">
                  <ul>
                    <li>Single property-carrying driver on the 70 hr / 8 day cycle.</li>
                    <li>No adverse driving conditions.</li>
                    <li>1 hour on duty at pickup and at drop-off.</li>
                    <li>Fuel stop every 1,000 miles, 30 minutes, logged on duty.</li>
                    <li>Required rests taken as 10 consecutive hours.</li>
                    {/* An assumption, not a fifth input: these are the US
                        federal maxima, so a real truck is almost always
                        smaller — and a route legal for the maximum is legal
                        for anything under it. */}
                    <li>
                        Standard 53&nbsp;ft dry van: 13&prime;6&Prime; high,
                        80,000&nbsp;lb, 5 axles, non-hazmat.
                    </li>
                    <li>Times shown in the home terminal&rsquo;s time zone.</li>
                  </ul>
                </div>
              </div>
            </div>

            <div
              className="layout__panel"
              role="tabpanel"
              id="panel-history"
              aria-labelledby="tab-history"
              hidden={tab !== "history"}
            >
              <TripHistory
                trips={history}
                activeId={trip?.id ?? null}
                onOpen={handleOpenTrip}
                waking={historyWaking}
                error={historyError}
                onRetry={refreshHistory}
              />
            </div>
          </div>

          <div className="layout__main">
            {loading && (
              <div className="placeholder">
                <div>
                  <div className="spinner" style={{ margin: "0 auto 14px" }} />
                  <h2>Planning the trip</h2>
                  <p>
                    Resolving locations, routing the legs, and applying the
                    hours-of-service limits.
                  </p>
                </div>
              </div>
            )}

            {!loading && !trip && (
              <>
                <PickerMap
                  picked={picked}
                  active={activeField}
                  onActiveChange={setActiveField}
                  onResolved={handlePicked}
                />

                <div className="placeholder placeholder--compact">
                  <div>
                    <h2>No trip planned yet</h2>
                    <p>
                      Set the driver&rsquo;s current location, the pickup and
                      drop-off, and how much of the 70-hour cycle is already
                      used. Spotter schedules the required breaks, fuel stops
                      and rests, then draws a compliant log sheet for every day
                      of the trip.
                    </p>
                  </div>
                </div>
              </>
            )}

            {!loading && trip && (
              <>
                <button type="button" className="backlink" onClick={handleBack}>
                  <span aria-hidden="true">&larr;</span> Plan another trip
                </button>

                <TripSummary trip={trip} />

                {/* Two different causes, two different actions: an outage is
                    worth retrying, a disconnected road network never will be. */}
                {trip.route.distances_estimated && (
                  <div className="alert alert--warn" role="status">
                    {trip.route.no_road_route ? (
                      <>
                        No drivable road route connects these locations, so
                        distances are straight-line estimates. Check the
                        waypoints are on the same road network — the
                        hours-of-service schedule is still calculated exactly.
                      </>
                    ) : (
                      <>
                        A routing service was unavailable, so distances are
                        straight-line estimates. The hours-of-service schedule
                        is still calculated exactly.
                      </>
                    )}
                  </div>
                )}

                {/* Said out loud rather than left to be assumed. A car route
                    is right about roads and wrong about vehicles: it does not
                    know a bridge is 12'4" or that a parkway bans lorries. The
                    schedule is exact either way — it is the road choice that
                    is unverified, and only the driver can judge that. */}
                {!trip.route.truck_routed && !trip.route.distances_estimated && (
                  <div className="alert alert--warn" role="status">
                    Routed on a <strong>car</strong> profile — bridge heights,
                    weight limits and lorry bans were not checked. Verify the
                    route before running it. The hours-of-service schedule is
                    calculated exactly regardless.
                  </div>
                )}

                {/* Side by side: the timeline is a long list, and stacking it
                    under the map pushed the log sheets far below the fold. */}
                <div className="results">
                  <TripMap trip={trip} />
                  <Timeline
                    timeline={trip.timeline}
                    cycleUsedAtStart={trip.summary.cycle_used_at_start}
                  />
                </div>

              </>
            )}
          </div>
        </div>

        {/* Outside .layout deliberately. The form column is irrelevant once you
            are reading logs, and a DOT grid is drawn at a fixed 940 units wide
            -- squeezed into the results column, two per row would render the
            hour numbers at about 4px. Full page width is what makes side by
            side legible. */}
        {!loading && trip && (
          <section className="logs">
            <div className="card">
              <div className="card__head">
                <span className="card__title">
                  Daily log sheets ({trip.daily_logs.length})
                </span>
                <div className="legend">
                  {DUTY_ROWS.map((row) => (
                    <span
                      className="legend__item"
                      key={row.status}
                      style={{ color: STATUS_COLOR[row.status] }}
                    >
                      <span className="legend__swatch" />
                      <span style={{ color: "var(--ink-soft)" }}>{row.label}</span>
                    </span>
                  ))}
                </div>
              </div>
            </div>

            <div className="sheets">
              {trip.daily_logs.map((log, index) => (
                <LogSheet
                  key={log.date}
                  log={log}
                  trip={trip}
                  index={index}
                  total={trip.daily_logs.length}
                />
              ))}
            </div>
          </section>
        )}
      </main>
    </>
  );
}
