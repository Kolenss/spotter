"""Django settings for the Spotter HOS trip planner.

Configuration is read from the environment so the same image runs locally and
on whichever host we deploy to, with safe development defaults.
"""

import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Django reads os.environ and nothing else, so a .env file would be silently
# ignored without this. Real environment variables still win, which is what we
# want on a host that injects them directly.
load_dotenv(BASE_DIR / ".env")


def env_flag(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY", "django-insecure-dev-only-key-replace-in-production"
)

DEBUG = env_flag("DJANGO_DEBUG", True)

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "trips",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Postgres in production, SQLite locally and in tests. The engine is the only
# thing that changes -- every model field in this app (Char, Float, Decimal,
# JSON, DateTime) maps natively to both, so no query or migration is affected.
#
# On Supabase, use the *session* pooler string (port 5432). The transaction
# pooler on 6543 cannot hold the prepared statements Django's migrations rely
# on, and the direct connection is IPv6-only, which most hosts cannot reach.
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        # Reuse connections for ten minutes rather than opening one per request.
        # Supabase's free tier caps concurrent connections (60 on nano), and a
        # cold connection to a pooler costs more than the query usually does.
        conn_max_age=600,
        # Drops a connection the database has already closed, instead of
        # handing a dead socket to the next request.
        conn_health_checks=True,
        ssl_require=env_flag("DATABASE_SSL_REQUIRE", False),
    )
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
USE_I18N = True

# A driver's record of duty status is kept in the time zone in effect at their
# home terminal, and stays in it even when the driver crosses other zones
# (Sec. 395.8). Every timestamp in this app is therefore naive local terminal
# time -- that is the domain's own model, not a shortcut, and it keeps the
# midnight boundaries that slice log sheets unambiguous.
TIME_ZONE = "UTC"
USE_TZ = False

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

REST_FRAMEWORK = {
    # JSON only. The browsable API would drag in django.contrib.auth, sessions
    # and templates for a backend whose sole client is the React SPA.
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    # This API is public and stateless, and django.contrib.auth is not
    # installed. Both of these must be emptied out or DRF tries to resolve
    # AnonymousUser from an app that is not there.
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
}

# The React dev server and the deployed frontend are on different origins.
CORS_ALLOWED_ORIGINS = env_list(
    "CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
)
CORS_ALLOW_ALL_ORIGINS = env_flag("CORS_ALLOW_ALL_ORIGINS", DEBUG)

CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
