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
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webapp.settings")

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "../static"]
STATIC_ROOT = os.path.join(BASE_DIR, "../collected_static/")
TEMPLATE_URL = "templates/"
TEMPLATEFILES_DIRS = [BASE_DIR / "templates"]
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "../media/"

LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/4.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = "django-insecure-hk_$^36%$mf=6^ndm7bb%c(nj&zrf!nq@h%!p==tbjc%e)6&_2"

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ["localhost", "192.168.1.6", "0.0.0.0"]

INTERNAL_IPS = ["localhost"]
CSRF_TRUSTED_ORIGINS = ["http://localhost", "http://192.168.1.6", "https://cadevil.org", "https://cadevil.at"]
APPEND_SLASH = False

# FIXME: This is for the config editor change save post request to work.
#        Maybe if we used a diff we could lower the number of fields sent?
DATA_UPLOAD_MAX_NUMBER_FIELDS = 8192

# Application definition
INSTALLED_APPS = [
    "daphne",
    "channels",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "model_manager",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "webapp.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": ["../templates"],
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

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [("127.0.0.1", 6379)],
        },
    },
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": True,
    "formatters": {
        "simple": {
            "format": "{levelname}:{name}:{message}",
            "style": "{",
        },
    },
    "handlers": {
        "in_memory_handler": {
            "level": "DEBUG",
            "class": "webapp.logger.InMemoryLogHandler",
            "formatter": "simple",
        },
        # you can have other handlers like 'console', 'file', etc.
    },
    "loggers": {
        "": {  # root logger
            "handlers": ["in_memory_handler"],
            "level": "DEBUG",
            "propagate": True,
        },
    },
}


ASGI_APPLICATION = "webapp.asgi.application"
WSGI_APPLICATION = "webapp.wsgi.application"

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

# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
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

MESSAGE_STORAGE = "django.contrib.messages.storage.cookie.CookieStorage"

# Do not keep the session open indefinitely
# so far a work around, should probably use timeouts
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

AUTH_USER_MODEL = "model_manager.CadevilUser"
# AUTH_GROUP_MODEL = "model_manager.CadevilGroup"

CELERY_RESULT_BACKEND = "django-db"
CELERY_CACHE_BACKEND = "django-cache"

# django setting.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379",
    },
    "db_cache": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "my_cache_table",
    }
}

# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True

# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Dict mapping user.id -> deque of log messages
USER_LOGS = defaultdict(lambda: deque(maxlen=1000))
