import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class PluginManagerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "plugin_manager"
    verbose_name = "Plugin Manager"

    def ready(self) -> None:
        # Run plugin discovery/sync as soon as the app is ready. Discovery
        # and hook errors are already isolated per-plugin inside the
        # registry, but we defensively wrap the call as well so that an
        # unexpected error (e.g. the database not being migrated yet) can
        # never crash Django startup.
        from plugin_manager.registry import registry

        try:
            registry.discover_and_sync()
        except Exception:  # noqa: BLE001 - startup must never crash because of plugins
            logger.exception("Plugin manager: unexpected error during plugin discovery.")
