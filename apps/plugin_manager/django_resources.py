"""Django adapter for the plugin manager's framework-independent resources."""
from importlib import import_module
from pathlib import Path

from django.apps import AppConfig
from django.conf import settings

from .resource_registry import ResourceRegistry


class PluginResourceConfig(AppConfig):
    """Register templates/static without importing a plugin's historical models."""
    def __init__(self, bundle):
        self.label = bundle.package.replace(".", "_") + "_resources"
        self.path = str(bundle.root)
        name = bundle.package + ".resources"
        super().__init__(name, import_module(name))
        self.verbose_name = bundle.plugin_id + " resources"
        self.default_auto_field = "django.db.models.BigAutoField"

    def import_models(self):
        # This adapter owns resources only, including if a package accidentally
        # adds models.py later. Domain models stay with the host's installed apps.
        self.models = self.apps.all_models[self.label]

    def ready(self):
        # Django's template/form loaders and AppDirectoriesFinder already use
        # AppConfig.path. Bolt's native static server instead reads these roots.
        static_root = Path(self.path) / "static"
        configured = list(settings.STATICFILES_DIRS)
        if static_root.is_dir() and static_root not in configured:
            settings.STATICFILES_DIRS = [*configured, static_root]
        settings.MIGRATION_MODULES = {**settings.MIGRATION_MODULES, self.label: None}


def resource_app_configs(project_root, configured_plugins):
    """Settings hook: let Django populate its registry through normal startup."""
    registry = ResourceRegistry.from_builtins(project_root, configured_plugins)
    return [PluginResourceConfig(bundle) for bundle in registry.bundles()]
