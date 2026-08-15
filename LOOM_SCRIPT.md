# Loom script — Spotter

**Core spine ≈ 740 words ≈ 4:50.** Everything tagged `[OPT]` is optional — about
90 seconds of it in total. Read the core only and you land inside the brief's
five minutes. Add `[OPT]` blocks back one at a time if you finish early on a
rehearsal, and drop them mid-take if you feel the clock.

Plan your demo trips before recording — a fresh plan takes ~70 seconds because
of the truck-stop lookup. Open them from History instead.

---

**The stack** *(say this over the app sitting idle)*

"Quickly, what it's built out of. The backend is Python and Django, serving a
JSON API through the Django REST framework. The rules engine inside it is plain
Python — no Django, no network calls — so the compliance maths can be tested on
its own, in milliseconds. The frontend is React and TypeScript, the maps are
Leaflet, and trips are stored in Postgres."

**The idea**

"Spotter takes four inputs and gives a truck driver a legal trip plan and a
filled-out logbook.

The four: where they are, where they pick up, where they drop off, and how many
hours they've already used."

**The math**

"Everything comes out of five federal limits. Eleven hours of driving. Inside a
fourteen-hour window. A thirty-minute break after eight hours of driving. Ten
hours off to reset. And seventy hours of work in any eight days — which you only
clear with a thirty-four-hour restart.

It all works in whole minutes, never decimal hours, which is why every log sheet
totals exactly twenty-four."

> `[OPT]` "One decision worth naming: when the seventy-hour cycle runs dry, we
> burn what's left before taking the restart rather than restarting early. On a
> long trip that's about nine and a half hours faster — the thirty-four hours
> costs the same whenever you take it, and it resets the other clocks too."

**The features**

> Demo the location picking here.

"You can type a location and pick from real suggestions, or click the map, or
drag the pin. Before we route anything we snap each point to the road network,
because a pin off the road isn't routable for a truck — and if that moves a
point more than sixty metres, we tell you."

**Where the data comes from, and what happens when I hit plan**

> Hit plan, or open a finished trip, and talk over it.

"**One — the place names.** As you type, we ask Nominatim. That's the search
engine for OpenStreetMap, the free map of the world anyone can edit. It gives
back real places with coordinates. Drop a pin instead and we run it backwards:
coordinates out, a town name back.

**Two — snapping.** Each point goes to OpenRouteService, which returns the
nearest spot on the *truck* road network. `[OPT]` It refuses anything more than
350 metres from a road, so if it can't snap a point, OSRM finds the nearest road
at all as a backup.

**Three — the route.** We ask OpenRouteService for a truck route, telling it what
we are: fifty-three-foot dry van, thirteen foot six high, eighty thousand
pounds, five axles. So it routes around low bridges, weight limits and lorry
bans. Back comes the line you see on the map, and two numbers per leg — miles,
and minutes.

And that's everything the outside world gives us. Coordinates, a line, miles,
minutes.

**Four — the schedule. This part is ours.** No service decides it."

**The engine**, because it's the whole app.

"It runs five clocks at once. Driving hours. The fourteen-hour window. Time
since the last break. Miles since fuel. And the seventy-hour cycle.

At every point it asks one question: *how long until the next of these runs out?*
The smallest answer wins. It drives exactly that far, then does whatever that
clock demands — thirty minutes for a break, ten hours for a rest, thirty-four
for a restart, thirty to fuel. An hour on duty at pickup, an hour at drop-off.

Two things make it read like a real logbook rather than a calculator.

First, working isn't the same as driving. Loading at pickup is on duty, but it
isn't driving — so it burns the fourteen-hour day and the seventy-hour cycle,
and leaves the eleven driving hours untouched.

Second, the break is usually free. The rule just wants thirty consecutive
minutes off the wheel — so the hour you spent loading already satisfies it. The
app labels that when it happens."

**Five — putting the stops on the map.** "The engine has no idea where on Earth
any of this is. It only knows a stop happened at, say, mile eight hundred and
twelve. To draw it, we walk that far along the route line and put a pin there.
The map is drawn from the schedule; the schedule never reads the map. So the
worst a map bug can do is misplace a pin — it can't change a number on a log
sheet."

**Six — the parking.** "For each forced rest we ask Overpass — another
OpenStreetMap service — for truck stops and rest areas in the fifty miles before
that point. Before, never after: stopping early is legal, stopping late means
you drove past a clock. Click one and the trip re-plans around it, and tells you
what it cost. Usually nothing — you stop earlier, so you start earlier."

**Seven — the log sheets.**

> Scroll to the sheets.

"These are never stored. Every time you open a trip, they're rebuilt from that
same duty timeline.

The timeline is one continuous ribbon, from the first minute of the trip to the
last. To make sheets out of it we cut it at every midnight — anything straddling
midnight gets split in two — and pad the front of the first day and the tail of
the last with off-duty time. That's what makes each sheet total exactly
twenty-four hours. The code checks that, and refuses to hand back a sheet that
doesn't.

Then it's drawn: four duty rows, twenty-four hour columns, the line stepping
between rows as the day goes on, totals down the right. `[OPT]` And underneath,
the Remarks strip — the city and state at every duty change, which is what the
regulation actually asks for."

**The trip timeline**

> Scroll the timeline beside the map.

"This is the trip in order — and the point is that it interleaves the driving
with the stops. A list of stops on its own reads as though they happen back to
back.

A driving row gives you the leg's miles, how far into the trip you are, and the
hours driven. A stop row gives you what it is, when, where, how long — and the
rule that forces it, cited to the section.

And down the side, a running count of the seventy-hour cycle, so you can watch
it drain away. `[OPT]` Rows where the cycle doesn't move leave it out rather
than repeat an unchanged number."

**The rest of the screen**

"Summary numbers up top — the cycle bar turns orange exactly when what's left
stops covering one more full day of work. And every plan is saved in History.

One honest limit: nobody publishes live parking availability. We show you where
the truck stops are, not whether there's a space tonight.

That's Spotter."
