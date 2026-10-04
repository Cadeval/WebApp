import os

from .base import *  # noqa: F401, F403

DEBUG = True

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
