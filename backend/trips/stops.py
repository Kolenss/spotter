"""Naming the kind of stop a duty event represents.

The engine emits duty events carrying a free-text note ("34-hour restart
(70-hour cycle exhausted)"); the API turns that into a coarse kind the UI can
colour, label and icon by.

It lives in its own module rather than inline in the serializer so the mapping
has exactly one home; the UI colours, labels and icons all key off this answer.
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
