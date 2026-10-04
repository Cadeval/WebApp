"""Django lifecycle adapter for the framework-independent plugin registry."""
from importlib import import_module
from pathlib import Path

from django.apps import AppConfig
from django.conf import settings

from .resource_registry import ResourceRegistry


class PluginAppConfig(AppConfig):
    """Let owned Django apps also expose their static roots to Bolt."""

    def ready(self):
        static_root = Path(self.path) / "static"
        configured = list(settings.STATICFILES_DIRS)
        if static_root.is_dir() and static_root not in configured:
            settings.STATICFILES_DIRS = [*configured, static_root]


class PluginResourceConfig(PluginAppConfig):
    """Register templates/static for plugins without Django persistence."""

    def __init__(self, bundle):
        self.label = bundle.package.replace(".", "_") + "_resources"
        self.path = str(bundle.root)
        name = bundle.package + ".resources"
        super().__init__(name, import_module(name))
        self.verbose_name = bundle.plugin_id + " resources"
        self.default_auto_field = "django.db.models.BigAutoField"

    def import_models(self):
        # Resource-only namespaces never register accidentally added models.
        self.models = self.apps.all_models[self.label]

    def ready(self):
        super().ready()
        settings.MIGRATION_MODULES = {**settings.MIGRATION_MODULES, self.label: None}


def resource_app_configs(project_root, configured_plugins):
    """Select trusted plugin adapters before normal Django app population."""
    registry = ResourceRegistry.from_builtins(project_root, configured_plugins)
    configs = []
    for bundle in registry.bundles():
        namespace = import_module(bundle.package + ".resources")
        declared = getattr(namespace, "DJANGO_APP_CONFIG", None)
        if declared is None:
            config = PluginResourceConfig(bundle)
        else:
            if not isinstance(declared, str) or not declared.startswith(bundle.package + "."):
                raise ValueError("Django adapter must belong to its plugin package.")
            config = AppConfig.create(declared)
            if not isinstance(config, PluginAppConfig) or not config.name.startswith(bundle.package + ".") or Path(config.path).resolve() != bundle.root.resolve():
                raise ValueError("Django adapter must use its owning plugin root.")
        configs.append(config)
    return configs
