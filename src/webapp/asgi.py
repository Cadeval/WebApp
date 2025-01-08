"""
ASGI config for webapp project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.0/howto/deployment/asgi/
"""

import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

# [FIXME] add handler for the result
_ = os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webapp.settings")

import os
import django
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
import model_manager.routing

# Initialize Django ASGI application early to ensure the AppRegistry
# is populated before importing code that may import ORM models.

django.setup()

application = ProtocolTypeRouter(
    {
        "http": get_asgi_application(),
        # "websocket": AllowedHostsOriginValidator(
        #     AuthMiddlewareStack(URLRouter(model_manager.routing.websocket_urlpatterns))
        # ),
        "websocket": URLRouter(model_manager.routing.websocket_urlpatterns),

    }
)