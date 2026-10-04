from django.apps import AppConfig


class SharedConfig(AppConfig):
    name = "apps.shared"

    def ready(self):
        from . import conversion_signals  # noqa: F401 - signal registration has intentional import side effects
        from .live_logs import install_handler
        install_handler()
