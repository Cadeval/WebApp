import os

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler  # noqa: E402
from django.urls import path  # noqa: E402
from djust.websocket import LiveViewConsumer  # noqa: E402

# Serve /static/* at the ASGI layer in both dev and prod.
http_app = ASGIStaticFilesHandler(django_asgi_app)

websocket_urlpatterns = [
    path("ws/live/", LiveViewConsumer.as_asgi()),
]

application = ProtocolTypeRouter(
    {
        "http": http_app,
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
        ),
    }
)
