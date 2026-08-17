import os
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# SECURITY — set SECRET_KEY in .env before deploying to production.
SECRET_KEY = os.environ.get("SECRET_KEY", "changeme")

# Comma-separated list of allowed hostnames. In production add your domain.
# Example: ALLOWED_HOSTS=myapp.com,www.myapp.com
ALLOWED_HOSTS = [
    h.strip() for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "daphne",
    "django.contrib.staticfiles",
    "djust",  # Core LiveView framework
    "apps.shared",  # BaseLiveView, context processors, theming
    "apps.mycelium",  # Home page — your first LiveView
]

# djust[theming] — 60+ theme packs, dark/light mode, CSS variable system.
try:
    import djust.theming  # noqa: F401

    INSTALLED_APPS.append("djust.theming")
except ImportError:
    pass

# djust[components] — buttons, cards, modals, tabs, and more.
try:
    import djust.components  # noqa: F401

    INSTALLED_APPS.append("djust.components")
except ImportError:
    pass

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

_context_processors = [
    "django.template.context_processors.debug",
    "django.template.context_processors.request",
    "django.contrib.auth.context_processors.auth",
    "django.contrib.messages.context_processors.messages",
]
try:
    import djust.theming.context_processors  # noqa: F401

    _context_processors.append("djust.theming.context_processors.theme_context")
except ImportError:
    pass

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "resources/templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": _context_processors,
        },
    },
]

ASGI_APPLICATION = "config.asgi.application"
WSGI_APPLICATION = "config.wsgi.application"

# DATABASE_URL — unset defaults to SQLite (fine for local dev, no action needed).
# Supported schemes:
#   sqlite:///db.sqlite3          → BASE_DIR-relative SQLite file
#   sqlite:////abs/path/db.sqlite → absolute path SQLite
#   postgres://user:pass@host/db  → PostgreSQL
#   postgresql://user:pass@host/db
_database_url = os.environ.get("DATABASE_URL", "")
if _database_url:
    _parsed = urlparse(_database_url)
    _scheme = (_parsed.scheme or "").lower()
    if _scheme in ("sqlite", "sqlite3"):
        _path = _parsed.path or ""
        if _path.startswith("//"):
            # four-slash: sqlite:////abs/path → /abs/path
            _path = _path[1:]
        elif _path.startswith("/"):
            # three-slash: sqlite:///mydb.db → treat as BASE_DIR relative
            _path = str(BASE_DIR / _path.lstrip("/"))
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": _path or str(BASE_DIR / "data/db-instance.sqlite3"),
            }
        }
    elif _scheme in ("postgres", "postgresql"):
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": _parsed.path.lstrip("/"),
                "USER": _parsed.username or "",
                "PASSWORD": _parsed.password or "",
                "HOST": _parsed.hostname or "localhost",
                "PORT": str(_parsed.port or 5432),
            }
        }
    else:
        raise ValueError(
            f"DATABASE_URL scheme {_scheme!r} not supported; "
            "use sqlite, sqlite3, postgres, or postgresql"
        )
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "data/db-instance.sqlite3",
        }
    }

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "resources/static"]

# Register every module that contains LiveView subclasses you want mountable
# over WebSockets. Unregistered views will silently fail to connect.
LIVEVIEW_ALLOWED_MODULES = ["apps.mycelium.views"]

# LiveView state backend.
# "memory"          — in-process, lost on restart. Fine for single-server dev.
# "redis://..."     — Redis. Required for multi-process or multi-server deploys.
# "rediss://..."    — Redis over TLS.
DJUST_STATE_BACKEND = os.environ.get("DJUST_STATE_BACKEND", "memory")
if DJUST_STATE_BACKEND.startswith(("redis://", "rediss://")):
    DJUST_REDIS_URL = DJUST_STATE_BACKEND

DJUST_CONFIG = {
    # T002: dj-root is auto-inferred — informational only.
    "suppress_checks": ["T002"],
}

# Fine-grained djust behaviour. All values below are the defaults — uncomment
# and change only what you need. Full reference: docs.djust.org/api/config/
# ─── Cookie namespace ────────────────────────────────────────────────────
# Cookies are scoped per-host, not per-port, so every app served from
# 127.0.0.1 (or any shared domain) otherwise shares Django's default
# `sessionid` / `csrftoken` and the djust theme cookies. Prefixing them with
# a per-project namespace keeps this app's session, CSRF token, and theme
# choice from colliding with other djust apps on the same host.
COOKIE_NAMESPACE = "djstart"
SESSION_COOKIE_NAME = f"{COOKIE_NAMESPACE}_sessionid"
CSRF_COOKIE_NAME = f"{COOKIE_NAMESPACE}_csrftoken"

LIVEVIEW_CONFIG = {
    # Namespace the four djust theme cookies (djust_theme, _preset, _pack,
    # _layout) as <ns>_djust_theme* so theme choices don't bleed across djust
    # apps sharing this host. djust's ThemeManager reads/writes the namespaced
    # cookies natively (theme.js write-side, theme_context read-side).
    "theme": {"cookie_namespace": COOKIE_NAMESPACE},
    # CSS framework djust-components emits classes for. Determines whether
    # {% dj_button %} / {% card %} / {% dj_input %} render Bootstrap-style
    # ('.btn .btn-danger', '.card .card-header') or Tailwind-style
    # ('px-4 py-2 bg-red-500', etc) markup. Component CSS is loaded
    # automatically by {% theme_head %} — no manual <link> needed.
    # Options: "bootstrap5" | "bootstrap4" | "tailwind" | "plain" | None
    "css_framework": "bootstrap5",
    # Event handler security mode.
    # "strict" — only @event_handler / @action decorated methods can be
    #            called (default, recommended).
    # "warn"   — allow undecorated methods but log a deprecation warning.
    # "open"   — no restriction (legacy, not recommended).
    # "event_security": "strict",
    # Set to False to fall back to HTTP polling instead of WebSockets.
    # "use_websocket": True,
    # Hot View Replacement — reloads LiveView Python code in dev without
    # disconnecting the client or losing state. Requires watchdog (in dev
    # deps). Dev-only: enabled in config/settings/dev.py — do NOT set here
    # in base.py because prod.py inherits base and would also pick it up
    # (prod doesn't want hot reload, and watchdog isn't installed there).
    # Rate limiting for WebSocket events (token bucket algorithm).
    # "rate_limit": {
    #     "rate": 100,                  # sustained events per second per connection
    #     "burst": 20,                  # burst allowance above the rate
    #     "max_connections_per_ip": 10, # max concurrent connections per IP
    # },
    # Maximum incoming WebSocket message size in bytes. 0 = no limit
    # (not recommended in production).
    # "max_message_size": 65536,  # 64 KB default
    # Enable detailed VDOM patching logs in the server console (dev only).
    # "debug_vdom": False,
    # Raise TypeError for non-serializable view state values instead of
    # silently coercing to str().
    # "strict_serialization": False,
}

# djust_theming.W001 — contrast ratio warnings on the built-in default preset.
# These are upstream issues in the theme pack, not fixable in user code.
SILENCED_SYSTEM_CHECKS = ["djust_theming.W001"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"  # e.g. "America/New_York", "Europe/London"
USE_I18N = True
USE_TZ = True
