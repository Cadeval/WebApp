"""Shared plugin compatibility policy for discovery, activation and routes."""
from django.conf import settings
COMPATIBILITY = {"debug", "production", "both"}

def compatible(value):
    return value == "both" or value == ("debug" if settings.DEBUG else "production")

def active_plugin(plugin_id):
    from .models import PluginRecord
    record = PluginRecord.objects.filter(plugin_id=plugin_id, enabled=True, error="", source=PluginRecord.Source.PACKAGE).first()
    return bool(record and record.environment_compatible)
