"""Django lifecycle for the BIM Workspace's models, resources and handlers."""
from pathlib import Path

from apps.plugin_manager.django_resources import PluginAppConfig


class BIMConfig(PluginAppConfig):
    name = "apps.plugins.bim_model_manager.django"
    label = "bim_model_manager"
    path = str(Path(__file__).resolve().parents[1])
    verbose_name = "BIM Workspace"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        super().ready()
        from . import signals  # noqa: F401 - register plugin-owned cleanup
