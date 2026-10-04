from django.apps import AppConfig


class SharedConfig(AppConfig):
    name = "shared"

    def ready(self):
        from .live_logs import install_handler
        install_handler()
