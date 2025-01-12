"""
Django settings for webapp project.

Generated by 'django-admin startproject' using Django 4.2.6.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/4.2/ref/settings/
"""

import os
import sys
from pathlib import Path
# log_storage
from collections import defaultdict, deque

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault(key="DJANGO_SETTINGS_MODULE", value="webapp.settings")

# =======================
# Basic Django Settings
# =======================

DEBUG = True
ALLOWED_HOSTS = ["localhost", "192.168.1.6", "0.0.0.0"]
INTERNAL_IPS = ["localhost"]
CSRF_TRUSTED_ORIGINS = ["http://localhost", "http://192.168.1.6", "https://cadevil.org", "https://cadevil.at"]
APPEND_SLASH = False

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
MEDIA_ROOT: Path = BASE_DIR / "../data/user_uploads/"

# =======================
# Security Settings
# =======================

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY: str = "django-insecure-hk_$^36%$mf=6^ndm7bb%c(nj&zrf!nq@h%!p==tbjc%e)6&_2"
CSRF_USE_SESSIONS = True
# =======================
# Middleware & Apps
# =======================

MIDDLEWARE: list[str] = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

INSTALLED_APPS: list[str] = [
    "daphne",
    "channels",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "drf_spectacular",
    "django_htmx",
    "model_manager",
]

# =======================
# URL and ASGI/WSGI Configuration
# =======================

ROOT_URLCONF: str = "webapp.urls"

ASGI_APPLICATION: str = "webapp.asgi.application"
WSGI_APPLICATION: str = "webapp.wsgi.application"

# =======================
# Templates Settings
# =======================

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": ["resources/templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# =======================
# Authentication Settings
# =======================

AUTH_USER_MODEL = "model_manager.CadevilUser"
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

MESSAGE_STORAGE: str = "django.contrib.messages.storage.cookie.CookieStorage"

# Do not keep the session open indefinitely
SESSION_EXPIRE_AT_BROWSER_CLOSE: bool = True

# =======================
# Celery Settings
# =======================

CELERY_RESULT_BACKEND: str = "django-db"
CELERY_CACHE_BACKEND: str = "django-cache"

# =======================
# REST Settings
# =======================
REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.BasicAuthentication',
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework.authentication.TokenAuthentication',
    ],
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'Your API Title',
    'DESCRIPTION': 'A detailed description of your API.',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': True,  # Only if you want to expose the schema itself (optional)
}

# =======================
# Logging Configuration
# =======================
# Logging: dict[str, int | bool | dict[str, str]] = {}
if os.environ.get("PRODUCTION") or os.environ.get("TESTING"):
    #     LOGGING = {
    #         "version": 1,
    #         "disable_existing_loggers": True,
    #         "formatters": {
    #             "simple": {
    #                 "format": "{levelname}:{name}:{message}",
    #                 "style": "{",
    #             },
    #         },
    #         "handlers": {
    #             "in_memory_handler": {
    #                 "level": "DEBUG",
    #                 "class": "webapp.logger.InMemoryLogHandler",
    #                 "formatter": "simple",
    #             },
    #             # you can have other handlers like 'console', 'file', etc.
    #         },
    #         "loggers": {
    #             "": {  # root logger
    #                 "handlers": ["in_memory_handler"],
    #                 "level": "DEBUG",
    #                 "propagate": True,
    #             },
    #         },
    #     }

    # Channels Redis Configuration
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [("127.0.0.1", 6379)],
            },
        },
    }

# =======================
# Cache Configuration
# =======================
Caches: dict[str, str] = {}
if os.environ.get("PRODUCTION") or os.environ.get("TESTING"):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": "redis://127.0.0.1:6379",
        },
        "db_cache": {
            "BACKEND": "django.core.cache.backends.db.DatabaseCache",
            "LOCATION": "my_cache_table",
        },
    }
elif os.environ.get("CICD"):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.db.DatabaseCache",
            "LOCATION": "my_cache_table",
        },
    }

# =======================
# Internationalization
# =======================

LANGUAGE_CODE: str = "en-us"
TIME_ZONE: str = "UTC"
USE_I18N: bool = True
USE_TZ: bool = True

# =======================
# Default Primary Key
# =======================

DEFAULT_AUTO_FIELD: str = "django.db.models.BigAutoField"

# =======================
# User Logs
# =======================

USER_LOGS: defaultdict[str, deque] = defaultdict(lambda: deque(maxlen=1000))

# =======================
# Database Settings
# =======================
# Database settings (will be overridden below in PRODUCTION block)
DATABASES: dict[str, dict[str, str]] = {}

if os.environ.get("PRODUCTION"):
    # Database
    # https://docs.djangoproject.com/en/4.2/ref/settings/#databases
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DB_NAME", "defaultdb"),
            "USER": os.environ.get("DB_USER", "doadmin"),
            "PASSWORD": os.environ.get("DB_PASSWORD"),
            "HOST": os.environ.get(
                "DB_HOST", "cadevil-pg-do-user-17774226-0.k.db.ondigitalocean.com"
            ),
            "PORT": "25060",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.path.join(BASE_DIR, "../data", "db-instance.sqlite3"),
            'OPTIONS': {
                'init_command': 'PRAGMA journal_mode=wal;',
            }
        }
    }

# =======================
# Data Upload
# =======================

# FIXME: This is for the config editor change save post request to work.
#        Maybe if we used a diff we could lower the number of fields sent?
DATA_UPLOAD_MAX_NUMBER_FIELDS: int = 8192
