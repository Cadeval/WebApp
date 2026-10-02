"""Django ASGI entry point; Bolt serves its own API runtime via runbolt."""
import os
from django.core.asgi import get_asgi_application
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
application = ASGIStaticFilesHandler(get_asgi_application())
