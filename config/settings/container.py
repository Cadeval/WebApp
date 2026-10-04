"""Production settings for the immutable image and its writable data volume."""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


_secret_file = os.environ.get("SECRET_KEY_FILE", "")
if _secret_file:
    if os.environ.get("SECRET_KEY"):
        raise ImproperlyConfigured("Set either SECRET_KEY or SECRET_KEY_FILE, not both.")
    try:
        _secret = Path(_secret_file).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as error:
        raise ImproperlyConfigured("Cannot read the configured SECRET_KEY_FILE.") from error
    os.environ["SECRET_KEY"] = _secret
    del _secret

from .prod import *  # noqa: E402, F401, F403

DEVELOPMENT_MCP_ENABLED = False
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
