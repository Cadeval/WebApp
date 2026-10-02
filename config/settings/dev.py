from .base import *  # noqa: F401, F403

DEBUG = True

# Permissive in dev — DEBUG=True already excludes this settings module from
# production. Lets you hit the server via 0.0.0.0, ngrok tunnels, LAN IP, etc.
ALLOWED_HOSTS = ["*"]

# State channel layer — in-process, no Redis needed in dev.
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

# Native WebSockets reject browser Origins unless explicitly configured.
# The log handler additionally requires Origin to equal the request Host.
CORS_ALLOWED_ORIGINS = ['http://127.0.0.1:8000', 'http://localhost:8000']

# Development-only read-only tools. DEBUG=False always prevents an MCP mount.
DEVELOPMENT_MCP_ENABLED = True
