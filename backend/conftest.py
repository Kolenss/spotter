"""Test-wide guarantees that hold no matter whose machine this runs on.

The suite is network-free by design: the routing and geocoding services are
free community servers with rate limits, and a suite that called them would be
both rude and flaky.

That is easy to state and easy to lose. ``config.settings`` calls
``load_dotenv()`` at import, so anything in a developer's ``backend/.env``
becomes part of the test environment -- which is how adding a real ORS key
silently turned six stubbed routing tests into live API calls, changing their
expected mileage and taking 24 seconds. Tests must not depend on whether the
person running them happens to hold a key.
"""

from __future__ import annotations

import pytest

from routing import ors


@pytest.fixture(autouse=True)
def no_truck_routing_key(monkeypatch):
    """Truck routing is off unless a test explicitly switches it on.

    Deleted rather than blanked so ``configured_key()`` takes the same "not
    configured" path a fresh checkout would.
    """
    monkeypatch.delenv(ors.API_KEY_ENV, raising=False)
    yield
