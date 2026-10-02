"""Reverse-only names for isolated native Bolt page tests."""
from config.api import api
from django_bolt.urls import build_urlpatterns

urlpatterns = build_urlpatterns(api)

from apps.plugin_manager.api import api as plugin_api
urlpatterns += build_urlpatterns(plugin_api)
