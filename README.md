# Spotter — HOS Trip Planner & ELD Log Generator

Takes a trip's details and produces an FMCSA hours-of-service compliant schedule
plus a drawn driver's daily log sheet for every day of the trip.

**Inputs** — current location, pickup location, drop-off location, current cycle
used (hrs).
**Outputs** — a map of the route with every stop marked, a stop-by-stop timeline
with each required break, fuel stop and rest, and one DOT log grid per calendar
day.

Django + Django REST Framework on the backend, React + TypeScript (Vite) on the
front. The rules engine is pure Python with no framework dependencies.

---

## Running it

Two terminals.

**Backend** (http://localhost:8000):

```bash
cd backend && pip install -r requirements.txt && python manage.py migrate && python manage.py runserver
```

**Frontend** (http://localhost:5173):

```bash
cd frontend && npm install && npm run dev
```

The frontend reads `VITE_API_BASE_URL` and defaults to `http://localhost:8000/api`.

## Tests

```bash
cd backend && python -m pytest
```

79 tests. The engine suite reproduces the completed log printed on pages 18–19
of the FMCSA driver's guide and asserts that every generated sheet totals
exactly 24.00 hours.

---

## The rules implemented

From the *Interstate Truck Driver's Guide to Hours of Service* (FMCSA, April
2022) and 49 CFR Part 395, for a property-carrying driver:

| Rule | Limit | Citation |
|---|---|---|
| Driving per window | 11 hours | § 395.3(a)(3) |
| Driving window | 14 consecutive hours from first work of the shift | § 395.3(a)(2) |
| Break | 30 consecutive minutes after 8 **cumulative** driving hours | § 395.3(a)(3)(ii) |
| Cycle | 70 on-duty hours in 8 rolling days | § 395.3(b) |
| Reset | 10 consecutive hours off duty | § 395.3(a)(1) |
| Restart | 34 consecutive hours off duty | § 395.3(c) |

Three details drive most of the correctness:

1. **Pickup, drop-off and fueling are On Duty (Not Driving)**, not off duty.
   § 395.2 counts loading, unloading and "inspecting, servicing, or
   conditioning any truck, including fueling it" as on-duty time. They consume
   the 14-hour window and the 70-hour cycle but not the 11-hour driving clock.
2. **Any non-driving block of 30+ consecutive minutes satisfies the required
   break** — off duty, on duty, or sleeper berth alike. A 30-minute fuel stop
   or the 1-hour pickup does double duty, which is why a fully compliant trip
   often shows zero standalone breaks. The UI labels these explicitly.
3. **The 14-hour window is consecutive wall-clock time.** It does not pause for
   meals, fuel or naps, and the 30-minute break does not extend it.

### Reading a log sheet

The 11-hour limit applies to a driving *window*, not a calendar day, and a
window straddles midnight freely. A single sheet can therefore show more than
11 driving hours when it holds the tail of one shift and the start of the next.
Where that happens the sheet's footer breaks the total down per shift.

## Stated assumptions

The brief fixes some of these; the rest are ours and are surfaced in the UI.

- Single property-carrying driver on the 70 hr / 8 day cycle. *(given)*
- No adverse driving conditions, so no § 395.1(b)(1) extension. *(given)*
- 1 hour on duty at pickup and at drop-off. *(given)*
- Fuel at least every 1,000 miles. *(given)*
- **Fuel stops last 30 minutes** and are logged on duty. *(ours — the brief
  gives the interval but no duration)*
- **Required rests are taken as a straight 10 consecutive hours** in the sleeper
  berth. The split sleeper-berth provision of § 395.1(g) is not implemented;
  straight 10-hour resets are fully compliant without it.
- **The driver starts rested**, with a fresh 14-hour window and only the entered
  cycle hours against the 70.
- **Trips start "now"** unless `start_time` is posted explicitly.
- **Rolling 70/8**: the entered cycle hours are held for the full 8-day window,
  since we know the total but not its day-by-day distribution. That is the
  conservative reading — it can never under-report cycle usage.
- All timestamps are the home terminal's local time, per § 395.8.

## The map

Leaflet with OpenStreetMap tiles — free and key-less, like the routing services.

Mid-route stops have no address of their own; what we know is how far along the
route they happened. [`routing/geometry.py`](backend/routing/geometry.py) walks
the road polyline that far to find each one, so a fuel stop at mile 1,000 lands
on the actual highway rather than on a straight line between cities.

The polyline sent to the browser is simplified with Ramer–Douglas–Peucker:
OSRM returns ~14,000 vertices for a cross-country route (~300 KB of JSON), which
reduces to ~600 with no visible change in shape, cutting the payload by 94%. The
**full-resolution** geometry stays in the database, because stop positions are
interpolated from it and simplifying first would move them.

## Not built yet

Deliberately out of scope, not oversights:

- **Team / two-driver trips.** All mutable clock state lives in a single
  `DriverState`, so a team is two instances plus swap logic rather than a
  rewrite.
- **Split sleeper berth** (§ 395.1(g)).
- **Reverse-geocoded remarks.** Mid-route stops are labelled by distance along
  the leg ("En route to Chicago, Illinois (761 mi)"). § 395.8 wants a city and
  state; the coordinates are now available to look one up.

---

## Layout

```
backend/
  hos/        Pure-Python rules engine — no Django imports, so it is testable
              in isolation. rules.py (constants), state.py (the driver's
              clocks), planner.py (the simulator), logsheet.py (day slicing).
  routing/    Nominatim geocoding + OSRM routing, with a haversine fallback.
              geometry.py locates stops along the route polyline.
  trips/      Django app: models, serializers, views.
frontend/
  src/components/LogSheetGrid.tsx   The DOT grid, drawn in SVG.
  src/components/TripMap.tsx        Leaflet route map with stop markers.
```

### Design notes

**Integer minutes everywhere.** The engine never uses float hours internally.
A day is 1440 minutes, so the four status totals land on exactly 24.00 instead
of 23.999999. `build_log_sheets` raises if a day fails to total 1440.

**Log sheets are derived, never stored.** They are rebuilt from the duty events
on every read, so a sheet cannot drift out of sync with its timeline.

**Routing degrades instead of failing.** OSRM and Nominatim are free community
servers with no SLA. Every call falls back to a great-circle estimate, and the
UI says so when that happens. An approximate mileage beats a 500.

### API

```
POST /api/trips/       plan and store a trip, returns the full computed payload
GET  /api/trips/{id}/  re-read a stored trip
GET  /api/trips/       recent trips
GET  /api/health/
```

```bash
curl -X POST http://localhost:8000/api/trips/ -H "Content-Type: application/json" -d '{"current_location":"Dallas, TX","pickup_location":"Houston, TX","dropoff_location":"Chicago, IL","current_cycle_used":12}'
```
