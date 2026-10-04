"""Compose native plugin routes for the Bolt runtime."""
from django_bolt import BoltAPI
from apps.shared.request_logging import configure_api_logging

api = BoltAPI(trailing_slash='keep', django_middleware=True)
configure_api_logging(api)

from apps.plugins.bim_model_manager.api import api as bim_api

# BIM is not an installed Django app; compose its native routes explicitly.
# Each handler keeps the runtime PluginRecord gate, including after disable.
api.mount('', bim_api)

from apps.shared.admin_logs import api as log_api
api.mount('', log_api)

from apps.plugins.browser_pages import api as browser_plugin_api
api.mount("", browser_plugin_api)

from apps.shared.security_metadata import api as security_api
api.mount('', security_api)

from apps.shared.container_health import api as health_api
api.mount('', health_api)

from apps.shared.development_mcp import mount_development_mcp
mount_development_mcp(api)
