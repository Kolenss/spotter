"""Naming the kind of stop a duty event represents.

The engine emits duty events carrying a free-text note ("34-hour restart
(70-hour cycle exhausted)"); the API turns that into a coarse kind the UI can
colour, label and icon by.

It lives here rather than in the serializer because the services layer needs the
same answer for a different reason -- deciding which stops are worth looking for
parking at -- and two copies of this mapping would eventually disagree about
whether a fuel stop is a rest.
"""

from __future__ import annotations

#: Maps an event's note onto a coarse kind the UI can icon and colour by.
def stop_kind(status: str, note: str) -> str:
    lowered = note.lower()
    if "pickup" in lowered:
        return "pickup"
    if "drop-off" in lowered:
        return "dropoff"
    if "fuel" in lowered:
        return "fuel"
    if "30-minute break" in lowered:
        return "break"
    if "34-hour" in lowered:
        return "restart"
    if "10-hour" in lowered:
        return "reset"
    return status


#: Stops the driver has to find somewhere to put an 80,000 lb truck for.
#:
#: Pickup and drop-off are deliberately absent: those happen at the shipper and
#: the receiver, which are addresses the driver was given, not places we get to
#: choose. Everything here is a stop the *regulation* forced, at a mile marker
#: rather than an address.
PARKABLE_KINDS = frozenset({"fuel", "break", "reset", "restart"})
