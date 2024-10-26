from django.urls import re_path

from model_manager import consumers

websocket_urlpatterns = [
    re_path(
        r"ws/model_manager/(?P<user_name>\w+)/$", consumers.SocketConsumer.as_asgi()
    ),
]
