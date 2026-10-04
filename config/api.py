"""Compose native plugin routes for the Bolt runtime."""
from django_bolt import BoltAPI
from django.conf import settings
from shared.request_logging import configure_api_logging

api = BoltAPI(trailing_slash='keep', django_middleware=True)
configure_api_logging(api)

from plugins.bim_model_manager.api import api as bim_api

# BIM owns a Django app; compose its native Bolt routes explicitly here.
# Each handler keeps the runtime PluginRecord gate, including after disable.
api.mount('', bim_api)

from shared.admin_logs import api as log_api
api.mount('', log_api)

from plugins.browser_pages import api as browser_plugin_api
api.mount("", browser_plugin_api)

from shared.security_metadata import api as security_api
api.mount('', security_api)

from shared.container_health import api as health_api
api.mount('', health_api)

if settings.DEBUG and getattr(settings, 'DEVELOPMENT_MCP_ENABLED', False):
    from plugins.development_mcp.native import mount_development_mcp
    mount_development_mcp(api)
