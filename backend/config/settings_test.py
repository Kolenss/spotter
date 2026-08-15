"""Settings for the test suite: everything from `config.settings`, on SQLite.

`.env` carries the deployed Postgres URL and `config.settings` loads it, so a
plain `pytest` against the normal settings builds and drops a `test_postgres`
database on the hosted server. That is slow, network-bound, rate-limited, and
one interrupted teardown away from leaving the stray database behind -- which
then fails every later run with "already exists".

Overriding it here rather than from a fixture is deliberate. pytest-django
configures Django during `pytest_load_initial_conftests`, ahead of any conftest
import, and Django's connection handler caches each connection's settings on
first use -- so a fixture that rewrites `settings.DATABASES` has already been
overtaken. Choosing the settings module is the only hook early enough.

SQLite here means Django builds the test database in memory, which is what
keeps the suite fast and free of network access.
"""

from .settings import *  # noqa: F401,F403
from .settings import BASE_DIR

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}
