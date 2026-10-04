"""Production settings for the immutable image and its writable data volume."""
import os
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from django.core.exceptions import ImproperlyConfigured


def runtime_secret(filename, setting):
    """Read a bounded mounted secret; errors never include its contents."""
    try:
        with Path(filename).open("rb") as source:
            raw = source.read(65537)
        if len(raw) > 65536:
            raise ValueError
        value = raw.decode("utf-8").rstrip("\r\n")
        if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError
        return value
    except (OSError, UnicodeError, ValueError):
        raise ImproperlyConfigured(f"Cannot read a valid {setting}.") from None


_secret_file = os.environ.get("SECRET_KEY_FILE", "")
if _secret_file:
    if os.environ.get("SECRET_KEY"):
        raise ImproperlyConfigured("Set either SECRET_KEY or SECRET_KEY_FILE, not both.")
    _secret = runtime_secret(_secret_file, "SECRET_KEY_FILE")
    os.environ["SECRET_KEY"] = _secret
    del _secret

# Keep Redis credentials out of Compose environment values and process argv.
_redis_secret_file = os.environ.get("REDIS_PASSWORD_FILE", "")
if _redis_secret_file:
    _redis_url = os.environ.get("REDIS_URL", "")
    try:
        _redis_parts = urlsplit(_redis_url)
        if _redis_parts.scheme not in {"redis", "rediss"} or not _redis_parts.hostname or _redis_parts.password is not None:
            raise ValueError
        _redis_parts.port
    except ValueError:
        raise ImproperlyConfigured("REDIS_PASSWORD_FILE requires a Redis URL without a password.") from None
    _redis_password = runtime_secret(_redis_secret_file, "REDIS_PASSWORD_FILE")
    _redis_username = quote(unquote(_redis_parts.username or ""), safe="")
    _redis_host = _redis_parts.netloc.rsplit("@", 1)[-1]
    _redis_location = urlunsplit(_redis_parts._replace(
        netloc=f"{_redis_username}:{quote(_redis_password, safe='')}@{_redis_host}"))
    del _redis_password

try:
    from .prod import *  # noqa: E402, F401, F403
finally:
    # Workers may import settings again; leave file-only credentials file-only
    # in inherited environment values rather than creating conflicting inputs.
    if _secret_file:
        os.environ.pop("SECRET_KEY", None)

_database_secret_file = os.environ.get("DATABASE_PASSWORD_FILE", "")
if _database_secret_file:
    if DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql" or urlsplit(os.environ.get("DATABASE_URL", "")).password is not None:  # noqa: F405
        raise ImproperlyConfigured("DATABASE_PASSWORD_FILE requires a PostgreSQL URL without a password.")
    DATABASES["default"]["PASSWORD"] = runtime_secret(_database_secret_file, "DATABASE_PASSWORD_FILE")  # noqa: F405

if DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql":  # noqa: F405
    DATABASES["default"].setdefault("OPTIONS", {})["connect_timeout"] = 5  # noqa: F405

if os.environ.get("REDIS_URL"):
    CACHES["default"].update({  # noqa: F405
        "KEY_PREFIX": "cadevil",
        "OPTIONS": {"socket_connect_timeout": 2, "socket_timeout": 2},
    })
    if _redis_secret_file:
        CACHES["default"]["LOCATION"] = _redis_location  # noqa: F405
        del _redis_location
    # PostgreSQL retains the durable session record; Redis serves cached reads.
    SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"

DEVELOPMENT_MCP_ENABLED = False
DATABASE_CONNECTION_LIFECYCLE = True
BOLT_MAX_UPLOAD_SIZE = 64 * 1024 * 1024
PLUGIN_BUILTINS = {
    key: value for key, value in PLUGIN_BUILTINS.items()  # noqa: F405
    if not key.startswith("cadevil.mcp.")
}

# This endpoint carries no user data and must be reachable by local probes.
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]

# Enable only behind a trusted proxy which strips incoming forwarded headers
# and supplies its own. The published backend port must be private to it.
if os.environ.get("CADEVIL_TRUST_PROXY_HTTPS", "false").lower() in {"1", "true", "yes", "on"}:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
