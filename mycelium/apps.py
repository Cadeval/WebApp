from django.apps import AppConfig


class HomeConfig(AppConfig):
    name = "mycelium"
    label = "mycelium"

    def ready(self):
        from plugin_manager.django_resources import register_landing_overview
        from plugin_manager.resource_registry import OverviewTemplate

        register_landing_overview(self, OverviewTemplate("mycelium/overview.html"))
