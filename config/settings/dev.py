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

# Hot View Replacement — reloads LiveView Python code in dev without
# disconnecting the client or losing state. Requires watchdog (in dev
# extras: `pip install -e ".[dev]"`). Declared explicitly so the intent
# is visible alongside the Makefile note about not running uvicorn with
# --reload (uvicorn's worker restart would defeat HVR's state preservation).
LIVEVIEW_CONFIG = {
    "hot_reload": True,  # file watcher on
    "hot_reload_auto_enable": True,  # call enable_hot_reload() from DjustConfig.ready()
    "hvr_enabled": True,  # v0.6.1 — state-preserving reload
}  # ty: ignore[invalid-assignment]
# FIXME: Fix live view config type inference
