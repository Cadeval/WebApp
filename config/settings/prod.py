import os

from .base import *  # noqa: F401, F403

DEBUG = False

# Required — no insecure default in production.
SECRET_KEY = os.environ["SECRET_KEY"]
if len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5 or SECRET_KEY.startswith("django-insecure-"):
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured("Production SECRET_KEY must be random, at least 50 characters, and not a development key.")

# Required — set to your domain(s), e.g. ALLOWED_HOSTS=myapp.com,www.myapp.com
ALLOWED_HOSTS = [
    h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()
]

# Required when behind a reverse proxy, load balancer, or PaaS.
# Example: CSRF_TRUSTED_ORIGINS=https://myapp.com,https://www.myapp.com
CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if o.strip()
]

# Force HTTPS and instruct browsers to remember it for 1 year.
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Prevent session and CSRF cookies from being sent over plain HTTP.
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Allow specific origins
CORS_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if o.strip()
]

# Credentialed requests use only the explicitly configured production origins.
CORS_ALLOW_ALL_ORIGINS = False

# Additional settings
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
CORS_ALLOW_HEADERS = ["Content-Type", "Authorization", "X-Requested-With"]
CORS_EXPOSE_HEADERS = ["X-Total-Count", "X-Page-Count"]
CORS_PREFLIGHT_MAX_AGE = 86400  # 24 hours; Bolt's supported setting name

# Optional shared Redis cache for deployments with multiple HTTP workers.
_redis_url = os.environ.get("REDIS_URL", "")
if _redis_url:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": _redis_url,
        }
    }

# Email — required for password reset and error reporting (ADMINS).
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@example.com")

# Error reporting — sends tracebacks to ADMINS when DEBUG=False.
# Format: "Name:email,Name2:email2"
_admins_raw = os.environ.get("ADMINS", "")
if _admins_raw:
    ADMINS = [tuple(a.split(":", 1)) for a in _admins_raw.split(",") if ":" in a]

# Development subprocess plugins must not contribute production resource apps.
PLUGIN_BUILTINS = {key: value for key, value in PLUGIN_BUILTINS.items()
                   if not key.startswith("cadevil.mcp.")}
from plugin_manager.django_resources import resource_app_configs
INSTALLED_APPS = [*INSTALLED_APPS, *resource_app_configs(BASE_DIR, PLUGIN_BUILTINS)]
