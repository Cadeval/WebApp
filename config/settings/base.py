import os
from pathlib import Path
from urllib.parse import unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Production requires a strong SECRET_KEY from the process environment.
SECRET_KEY = os.environ.get("SECRET_KEY", "changeme")

# Comma-separated list of allowed hostnames. In production add your domain.
# Example: ALLOWED_HOSTS=myapp.com,www.myapp.com
ALLOWED_HOSTS = [
    h.strip() for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
]

# =======================
#  Middleware & Apps
# =======================


MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_bolt",  # Native HTTP/WebSocket router
    "shared",  # Identity, access, logging and generic pages
    "mycelium",  # Home, sessions and user settings
    "plugin_manager",
]

# =======================
# URL and ASGI/WSGI Configuration
# =======================

ROOT_URLCONF: str = "config.urls"

ASGI_APPLICATION = "config.asgi.application"
WSGI_APPLICATION = "config.wsgi.application"

# Redirect URLs
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

# =======================
# Paths and Directories
# =======================

STATIC_URL: str = "/static/"
STATICFILES_DIRS: list[Path] = [BASE_DIR / "resources/static"]
STATIC_ROOT: str = os.path.join(BASE_DIR, "resources/collected_static/")

# Bolt automatically mounts MEDIA_ROOT ahead of route guards when MEDIA_URL
# has a nonempty prefix. An absolute "/" disables that mount in Bolt 0.11;
# an empty string would be expanded by Django to SCRIPT_NAME and is unsafe.
# Uploaded files are delivered only by the owner-checked download routes.
MEDIA_URL: str = "/"
MEDIA_ROOT: Path = BASE_DIR / "data/user_uploads/"

# OpenStudio energy simulations. Leave the CLI path empty to let the
# installed Python package or PATH provide it.
OPENSTUDIO_CLI_PATH: str | None = os.environ.get("OPENSTUDIO_CLI_PATH") or None
OPENSTUDIO_TIMEOUT_SECONDS: int = int(
    os.environ.get("OPENSTUDIO_TIMEOUT_SECONDS", "900")
)


# =======================
# Templates Settings
# =======================
_context_processors = [
    "django.template.context_processors.debug",
    "django.template.context_processors.request",
    "django.contrib.auth.context_processors.auth",
    "django.contrib.messages.context_processors.messages",
    "plugin_manager.context_processors.plugin_nav_items",
    "plugin_manager.context_processors.plugin_editor_items",
]

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

# =======================
# Authentication Settings
# =======================

AUTH_USER_MODEL = "shared.CadevilUser"

AUTH_PASSWORD_VALIDATORS: list[dict[str, str]] = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# =======================
# django-bolt Settings
# =======================

# Browser pages use Django sessions, CSRF checks and explicit route guards.
MESSAGE_STORAGE: str = "django.contrib.messages.storage.cookie.CookieStorage"

# Do not keep the session open indefinitely
SESSION_EXPIRE_AT_BROWSER_CLOSE: bool = True


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
            _path = str(_path.lstrip("/"))
            _path = str(BASE_DIR / _path)
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": _path or str(BASE_DIR / "data/db-instance.sqlite3"),
                "OPTIONS": {
                    "init_command": "PRAGMA journal_mode=wal;",
                },
            }
        }
    elif _scheme in ("postgres", "postgresql"):
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": unquote(_parsed.path.lstrip("/")),
                "USER": unquote(_parsed.username or ""),
                "PASSWORD": unquote(_parsed.password or ""),
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
            "OPTIONS": {
                "init_command": "PRAGMA journal_mode=wal;",
            },
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"  # e.g. "America/New_York", "Europe/London"
USE_I18N = True
USE_TZ = True

# =======================
# Data Upload
# =======================

# FIXME: This is for the config editor change save post request to work.
#        Maybe if we used a diff we could lower the number of fields sent?
DATA_UPLOAD_MAX_NUMBER_FIELDS: int = 8192

# =======================
# Plugin System Settings
# =======================

# The API version implemented by this host application. Plugins declare the
# API version they were built against in their PluginManifest, and are only
# activated when their major version matches this one. Kept in settings so
# it can be overridden per-deployment if ever needed.
PLUGIN_API_VERSION: str = "1.0"
PLUGIN_MAX_UPLOAD_SIZE: int = 2 * 1024 * 1024

# Plugins shipped with the application use the same manifest contract as
# third-party ``cadevil.plugins`` entry points, but do not require this source
# checkout to be installed as a Python distribution before they can be tested.
PLUGIN_BUILTINS: dict[str, str] = {
    "cadevil.example.editor": "example_plugin:plugin_manifest",
    "cadevil.rust-example.editor": "rust_example_plugin:plugin_manifest",
    "cadevil.bim.model_manager": "bim_model_manager:plugin_manifest",
    "cadevil.mcp.context7": "development_mcp:context7_manifest",
    "cadevil.mcp.git": "development_mcp:git_manifest",
    "cadevil.mcp.native": "development_mcp:native_manifest",
    "cadevil.mcp.ui_ux": "development_mcp:ui_ux_manifest",
    "cadevil.mcp.code_audit": "development_mcp:code_audit_manifest",
}

# Native geometry workers per assessment; bounded to avoid oversubscribing HTTP workers.
IFC_GEOMETRY_THREADS = int(os.environ.get('IFC_GEOMETRY_THREADS', '4'))

# Overlap CPU-heavy schema validation with geometry/calculation for large IFCs.
IFC_PARALLEL_VALIDATION = os.environ.get('IFC_PARALLEL_VALIDATION', 'true').lower() in {'1', 'true', 'yes', 'on'}
IFC_PARALLEL_VALIDATION_MIN_BYTES = int(os.environ.get('IFC_PARALLEL_VALIDATION_MIN_BYTES', '2000000'))

# Fixed disclosure defaults live in .well-known/security.txt. Override these
# per deployment; empty Canonical/Policy fields avoid guessing a public host.
SECURITY_TXT_CONTACT = os.environ.get('SECURITY_TXT_CONTACT', '')
SECURITY_TXT_EXPIRES = os.environ.get('SECURITY_TXT_EXPIRES', '')
SECURITY_TXT_CANONICAL = os.environ.get('SECURITY_TXT_CANONICAL', '')
SECURITY_TXT_POLICY = os.environ.get('SECURITY_TXT_POLICY', '')

# Diagnostic logs are structured for collection and bounded for the admin UI.
# Never include request payloads, credentials or uploaded model attributes.
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
ADMIN_LOG_ENABLED = os.environ.get('ADMIN_LOG_ENABLED', 'true').lower() in {'1', 'true', 'yes', 'on'}
ADMIN_LOG_PATH = Path(os.environ.get('ADMIN_LOG_PATH', str(BASE_DIR / 'data/live-logs.sqlite3')))
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {'correlation': {'()': 'shared.logging_utils.CorrelationFilter'}},
    'formatters': {
        'json': {'()': 'shared.logging_utils.SafeJSONFormatter'},
        'live': {'()': 'shared.logging_utils.SafeTextFormatter'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'json', 'filters': ['correlation']},
        'live': {'class': 'shared.live_logs.SharedLogHandler', 'formatter': 'live', 'level': 'INFO'},
        'null': {'class': 'logging.NullHandler'},
    },
    'root': {'handlers': ['console', 'live'], 'level': LOG_LEVEL},
    'loggers': {
        # Override Django's default console/mail handlers so raw exception
        # messages cannot bypass the safe formatter or duplicate each entry.
        'django': {'handlers': [], 'propagate': True, 'level': LOG_LEVEL},
        # Bolt gates native raw-path access logs on isEnabledFor(WARNING).
        # Our middleware supplies route templates, durations and request IDs.
        'django.server': {'handlers': ['null'], 'propagate': False, 'level': 'CRITICAL'},
        # SQL diagnostics contain parameter values; keep them out even when
        # development or a deployment enables DEBUG-level application logs.
        'django.db.backends': {'handlers': ['null'], 'propagate': False, 'level': 'CRITICAL'},
        'django_bolt': {'handlers': [], 'propagate': True, 'level': LOG_LEVEL},
    },
}
