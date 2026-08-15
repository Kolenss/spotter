# Spotter — HOS Trip Planner & ELD Logs

Django + React app: four trip inputs → an FMCSA-compliant schedule, a route map,
and a drawn DOT log sheet per day. Vault note: `[[spotter]]`.

## Architectural Decisions
- [2026-08-14] `hos/` is pure Python with zero Django imports — engine testable with no DB, settings, or network
- [2026-08-14] Engine works in integer minutes, never float hours — guarantees log sheets total exactly 24.00, not 23.999999
- [2026-08-14] Engine holds no coordinates; positions are interpolated onto the route polyline at serialization — a map bug cannot corrupt a log sheet
- [2026-08-14] Daily log sheets are derived at read time, never persisted — they cannot drift from the duty timeline
- [2026-08-14] Django calls Nominatim/OSRM server-side, not the browser — Nominatim requires a User-Agent, which browser JS cannot set
- [2026-08-14] `USE_TZ = False`; all timestamps are naive home-terminal local time per § 395.8
- [2026-08-14] Timeline API interleaves driving legs with stops — a stops-only list reads as though the stops happen back to back
- [2026-08-15] Locations resolve to coordinates *before* planning (search pick or map pin); `_resolve()` skips geocoding when lat/lon arrive, since re-geocoding a label could land on a different town of the same name
- [2026-08-15] `Trip` already stored `current/pickup/dropoff_lat/lon` from the map work, so map picking needed **no migration**
- [2026-08-15] Half a coordinate pair is a 400, not a silent fallback to geocoding the text — the alternative places the trip somewhere the driver did not choose
- [2026-08-15] Django proxies Nominatim `/search` and `/reverse` at `/api/places/*`; they live in `trips/` because `routing/` is deliberately a headless library layer
- [2026-08-15] Log sheets render outside `.layout` at full page width — a DOT grid is authored 940 units wide, so two-up inside the results column would draw hour numbers at ~4px
- [2026-08-15] `DATABASES` comes from `DATABASE_URL` via `dj-database-url`, defaulting to SQLite — tests stay on SQLite deliberately so they remain fast and network-free

## Solutions & Fixes
- [2026-08-14] DRF without `django.contrib.auth` needs `DEFAULT_AUTHENTICATION_CLASSES: []` and `UNAUTHENTICATED_USER: None`, else AnonymousUser fails to import
- [2026-08-14] Leaflet caches container size at creation; built before layout settles it renders into a fraction of the frame forever — fix with ResizeObserver → `invalidateSize()`
- [2026-08-14] OSRM returns ~14k vertices per cross-country route (~300 KB JSON); Ramer–Douglas–Peucker cuts it to ~625 points / 19 KB with no visible change
- [2026-08-14] Keep full-resolution geometry in the DB — stop positions interpolate from it, so simplifying before storage would move them
- [2026-08-14] RDP must be iterative, not recursive — 14k points blows the call stack
- [2026-08-14] `runserver --noreload` silently serves stale code after edits; use plain `runserver`
- [2026-08-15] Reverse geocoding costs ~1.2s, so a pinned marker lands immediately with its coordinates as a provisional label and the place name replaces it on arrival
- [2026-08-15] Concurrent reverse lookups need a per-field token — at 1.2s a corrected pin easily has two in flight, and the slower first reply would land last and overwrite the correction
- [2026-08-15] Leaflet `divIcon`s must be cached, not rebuilt per render: react-leaflet calls `setIcon()` on identity change, which replaces the marker's DOM node and tears down the drag handler
- [2026-08-15] `fitBounds` on a single point makes degenerate bounds and zooms to maxZoom (19, one building) — cap with `maxZoom: 12`
- [2026-08-15] A grid child that must fill but not *define* the row height needs `height: 0; min-height: 100%` — otherwise a long timeline sets the row and stretches the map to match
- [2026-08-15] `margin-inline: auto` computes to 0 once a block is wider than its containing block, pinning it left and spilling right; use `calc((100% - width) / 2)` to break out symmetrically
- [2026-08-15] `100vw` counts the scrollbar, so a full-bleed element needs a gutter wider than the scrollbar or it overflows by exactly its width
- [2026-08-15] Django reads `os.environ` only — a `.env` is silently ignored without `python-dotenv`'s `load_dotenv()`
- [2026-08-15] Supabase: use the **session** pooler (5432). Transaction pooler (6543) cannot hold the prepared statements `migrate` needs; the direct connection is IPv6-only
- [2026-08-15] Supabase's connection string shows `[YOUR-PASSWORD]` — the square brackets are placeholder markers and must be deleted, and `@`/`#` in the password must be percent-encoded or the URL silently mis-parses
- [2026-08-15] python-dotenv keeps a mid-value `#`, but ` #` (whitespace first) starts a comment and truncates the value
- [2026-08-15] `DJANGO_DEBUG=false` locally also makes `ALLOWED_HOSTS` mandatory and flips `CORS_ALLOW_ALL_ORIGINS` off — both then need real values or every request 400s

## Conventions
- [2026-08-14] Tests live in a `tests/` package per app, not a single `tests.py`
- [2026-08-14] Routing calls always degrade to a haversine estimate rather than raising; the response flags `distances_estimated`
- [2026-08-14] Duty-status colours are identical across the log grid, the timeline, and the map markers
- [2026-08-15] `STOP_LABEL` in `types.ts` names each stop kind once, shared by the map legend, map tooltips and the timeline — same anti-drift rule as the colours
- [2026-08-15] Nominatim autocomplete is debounced 500 ms with a 3-character floor, and the in-flight request is aborted on each keystroke — their policy caps at 1 req/sec and discourages autocomplete outright
- [2026-08-15] Editing a location's text clears its stored coordinates; only picking a suggestion, pinning, or dragging sets them
- [2026-08-15] The timeline shows the short stop name with its reason on a separate line, never the engine's note — the note's parenthetical duplicates the reason and gets truncated away

## Domain Context (49 CFR Part 395)
- [2026-08-14] Pickup, drop-off and fueling are On Duty (Not Driving) — they burn the 14-hr window and 70-hr cycle but not the 11-hr driving clock
- [2026-08-14] Any non-driving block ≥30 consecutive min satisfies the required break, so a compliant trip often shows zero standalone breaks
- [2026-08-14] The 11-hr limit is per shift, not per calendar day — a sheet can legally show 13–14 driving hours across two shifts
- [2026-08-14] Violations occur only if you *drive* past 70; working past it on-duty-not-driving is legal (guide p.10)
- [2026-08-14] Greedy (burn the remaining cycle, then restart) beats restart-first by 9.5 hrs — the 34 hrs costs the same either way and also resets the 11/14 clocks

## Outstanding
- [2026-08-14] Git root is `Documents\GitHub`, not `spotter/` — needs its own `git init` before pushing standalone; nothing committed yet
- [2026-08-14] Remaining deliverables: hosted version, 3–5 min Loom, GitHub link
- [2026-08-14] Deploy needs Postgres (`dj-database-url`) — SQLite is ephemeral on Render/Railway
- [2026-08-15] Supabase project `spotter` created: ref `wdrgnvabfkyjxoqfywdy`, us-east-1, free tier — awaiting its `DATABASE_URL` in `backend/.env`, then `migrate`
- [2026-08-15] Region chosen as us-east-1 to match a Render Virginia service; Supabase has no Oregon region, so Render's default region would cost a ~20ms hop per query
- [2026-08-15] Render's free tier sleeps after 15 min idle with a 30–50s cold start — a grader opening it cold waits ~1 min before the first plan
- [2026-08-15] No auth by design (brief specifies none): `GET /api/trips/` returns everyone's trips and sequential integer IDs are enumerable — worth naming in the Loom as a decision
- [2026-08-15] `route_geometry` is ~448 KB per trip and is essentially the whole DB size — a 1 GB free Postgres tier holds roughly 2,000 trips
