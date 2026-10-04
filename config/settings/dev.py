import os

from .base import *  # noqa: F401, F403

DEBUG = True
PLUGIN_CA_PUBLIC_URL = os.environ.get("PLUGIN_CA_PUBLIC_URL", "http://127.0.0.1:8000")

# Permissive in dev — DEBUG=True already excludes this settings module from
# production. Lets you hit the server via 0.0.0.0, ngrok tunnels, LAN IP, etc.
ALLOWED_HOSTS = ["*"]

# Native WebSockets reject browser Origins unless explicitly configured.
# The log handler additionally requires Origin to equal the request Host.
CORS_ALLOWED_ORIGINS = ['http://127.0.0.1:8000', 'http://localhost:8000']

# Development-only read-only tools. DEBUG=False always prevents an MCP mount.
DEVELOPMENT_MCP_ENABLED = True

# Installed developer tools only; not part of the application's production dependencies.
DEVELOPMENT_MCP_TOOL_ROOT = os.environ.get("CADEVIL_MCP_TOOL_ROOT", "/Users/mia/Documents/ChatGPT/CadEval/mcp_tools")
DEVELOPMENT_MCP_CONTEXT7_PORT = 8017
DEVELOPMENT_MCP_GIT_PORT = 8018

DEVELOPMENT_MCP_UI_UX_PORT = 8019
DEVELOPMENT_MCP_CODE_AUDIT_PORT = 8020

# Installed read-only provider; site administrators can disable its debug plugin.
# All calls are constrained to the reviewed Cadevil Compose project.
DEVELOPMENT_MCP_DOCKER_ENABLED = os.environ.get("CADEVIL_MCP_DOCKER_ENABLED", "1") == "1"
DEVELOPMENT_MCP_DOCKER_PORT = 8022
DEVELOPMENT_MCP_DOCKER_HOST = os.environ.get("CADEVIL_MCP_DOCKER_HOST", "ssh://codex@meanderingmind.me:25519")

# Register resource bundles after choosing the environment's installed plugins.
from plugin_manager.django_resources import resource_app_configs
INSTALLED_APPS = [*INSTALLED_APPS, *resource_app_configs(BASE_DIR, PLUGIN_BUILTINS)]
