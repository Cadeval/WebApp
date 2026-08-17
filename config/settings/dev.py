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
    **LIVEVIEW_CONFIG,  # noqa: F405 — base.py exports this via `import *`
    "hvr_enabled": True,
}
