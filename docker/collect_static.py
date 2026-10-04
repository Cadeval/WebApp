"""Collect public assets without starting application plugins or using secrets."""
from pathlib import Path

import django
from django.conf import settings
from django.core.management import call_command

root = Path(__file__).resolve().parent.parent
settings.configure(
    INSTALLED_APPS=[
        "django.contrib.admin.apps.SimpleAdminConfig", "django.contrib.auth",
        "django.contrib.contenttypes", "django.contrib.sessions", "django.contrib.messages",
        "django.contrib.staticfiles",
    ],
    STATIC_URL="/static/", STATIC_ROOT=str(root / "resources/collected_static"),
    # Bolt 0.11.1's native server searches STATIC_ROOT followed by
    # STATICFILES_DIRS even with DEBUG=False. Collect only installed Django
    # assets; first-party public assets stay in their original served tree.
    STATICFILES_DIRS=[],
)
django.setup()
call_command("collectstatic", interactive=False, verbosity=0)
