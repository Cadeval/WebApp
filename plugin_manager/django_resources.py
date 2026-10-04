"""Django lifecycle adapter for the framework-independent plugin registry."""
from importlib import import_module
from pathlib import Path

from django.apps import AppConfig, apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.template.loader import get_template

from .resource_registry import ResourceRegistry, validate_overview


def register_landing_overview(config, overview):
    """Register one owned fragment when a trusted Django application loads."""
    bundle = getattr(config, "resource_bundle", None)
    namespace = (bundle.package.removeprefix("plugins.") if bundle else config.name).replace(".", "/")
    validate_overview(overview, config.path, namespace)
    existing = getattr(config, "landing_overview", None)
    if existing is not None and existing != overview:
        raise ValueError("An application may register only one landing overview.")
    config.landing_overview = overview


def _loaded_overview(config, *, include_debug=False):
    overview = getattr(config, "landing_overview", None)
    compatible = {"production", "both", "debug"} if include_debug else {"production", "both"}
    if overview is None or overview.compatibility not in compatible:
        return None
    bundle = getattr(config, "resource_bundle", None)
    namespace = (bundle.package.removeprefix("plugins.") if bundle else config.name).replace(".", "/")
    expected = validate_overview(overview, config.path, namespace)
    template = get_template(overview.template_name)
    if Path(template.origin.name).resolve() != expected.resolve():
        raise ImproperlyConfigured("Landing overview loader must resolve the owning application's template.")
    return overview.template_name


def public_overview_templates():
    """Expose installed production descriptions without activating any tool."""
    return tuple(template for config in sorted(apps.get_app_configs(), key=lambda value: value.name)
                 if (template := _loaded_overview(config)) is not None)


def get_overview_for_plugin(plugin_id):
    """Return a trusted plugin's owned description for its catalog details."""
    for config in apps.get_app_configs():
        bundle = getattr(config, "resource_bundle", None)
        if bundle is not None and bundle.plugin_id == plugin_id:
            return _loaded_overview(config, include_debug=settings.DEBUG)
    return None


class PluginAppConfig(AppConfig):
    """Let owned Django apps also expose their static roots to Bolt."""

    def ready(self):
        static_root = Path(self.path) / "static"
        configured = list(settings.STATICFILES_DIRS)
        if static_root.is_dir() and static_root not in configured:
            settings.STATICFILES_DIRS = [*configured, static_root]
        bundle = getattr(self, "resource_bundle", None)
        if bundle is not None and bundle.overview is not None:
            register_landing_overview(self, bundle.overview)


class PluginResourceConfig(PluginAppConfig):
    """Register templates/static for plugins without Django persistence."""

    def __init__(self, bundle):
        self.label = bundle.package.replace(".", "_") + "_resources"
        self.path = str(bundle.root)
        name = bundle.package + ".resources"
        super().__init__(name, import_module(name))
        self.verbose_name = bundle.plugin_id + " resources"
        self.default_auto_field = "django.db.models.BigAutoField"
        self.resource_bundle = bundle

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
        config.resource_bundle = bundle
        configs.append(config)
    return configs
