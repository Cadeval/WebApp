import os
from pathlib import Path
from urllib.parse import urlparse

from django_bolt import IsAuthenticated, JWTAuthentication

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# SECURITY — set SECRET_KEY in .env before deploying to production.
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
    "daphne",
    "django.contrib.staticfiles",
    "django_bolt",  # Core LiveView framework
    "django_htmx",
    "apps.shared",  # BaseLiveView, context processors, theming
    "apps.mycelium",  # Home page — your first LiveView
    "apps.plugin_manager",
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

STATIC_URL: str = "static/"
STATICFILES_DIRS: list[Path] = [BASE_DIR / "resources/static"]
STATIC_ROOT: str = os.path.join(BASE_DIR, "resources/collected_static/")

TEMPLATE_URL: str = "templates/"
TEMPLATEFILES_DIRS: list[Path] = [BASE_DIR / "resources/templates"]

MEDIA_URL: str = "user_uploads/"
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
    "apps.plugin_manager.context_processors.plugin_nav_items",
    "apps.plugin_manager.context_processors.plugin_editor_items",
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
# AUTH_GROUP_MODEL = "model_manager.CadevilGroup"

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

# Global defaults applied to every BoltAPI route that doesn't override
# `auth=`/`guards=` explicitly (see apps/mycelium/api.py). Routes that must
# stay public (e.g. the login page/endpoint) opt out with `guards=[AllowAny()]`.
# `secret=SECRET_KEY` is passed explicitly (instead of leaving JWTAuthentication
# fall back to `django.conf.settings.SECRET_KEY` on its own) because settings
# are still being assembled at this point — reading `django.conf.settings`
# here would re-enter Django's settings setup.
# BOLT_AUTHENTICATION_CLASSES = [
#     JWTAuthentication(cookie="access_token", secret=SECRET_KEY),
#     JWTAuthentication(secret=SECRET_KEY),
# ]
# BOLT_DEFAULT_PERMISSION_CLASSES = [IsAuthenticated()]

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
            "OPTIONS": {
                "init_command": "PRAGMA journal_mode=wal;",
            },
        }
    }

STATIC_URL = "/static/"
# STATIC_ROOT = str(BASE_DIR / "resources/static")
STATICFILES_DIRS = [BASE_DIR / "resources/static"]

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
}

# =======================
# Celery Settings
# =======================

CELERY_RESULT_BACKEND: str = "django-db"
CELERY_CACHE_BACKEND: str = "django-cache"

# Native geometry workers per assessment; bounded to avoid oversubscribing HTTP workers.
IFC_GEOMETRY_THREADS = int(os.environ.get('IFC_GEOMETRY_THREADS', '4'))

# Overlap CPU-heavy schema validation with geometry/calculation for large IFCs.
IFC_PARALLEL_VALIDATION = os.environ.get('IFC_PARALLEL_VALIDATION', 'true').lower() in {'1', 'true', 'yes', 'on'}
IFC_PARALLEL_VALIDATION_MIN_BYTES = int(os.environ.get('IFC_PARALLEL_VALIDATION_MIN_BYTES', '2000000'))
