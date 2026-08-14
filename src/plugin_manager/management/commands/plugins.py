from django.core.management.base import BaseCommand, CommandError

from plugin_manager.models import PluginActivationError, PluginRecord
from plugin_manager.registry import registry
from plugin_manager.services import manage_plugin, reload_plugins


class Command(BaseCommand):
    help = "Inspect and manage cadevil plugins discovered via the 'cadevil.plugins' entry point group."

    def add_arguments(self, parser) -> None:
        subparsers = parser.add_subparsers(dest="action", required=True)
        subparsers.add_parser("list", help="List all discovered plugins and their state.")
        subparsers.add_parser("discover", help="Re-run plugin discovery and sync plugin records.")
        subparsers.add_parser("reload", help="Invalidate caches and re-run plugin discovery.")

        enable_parser = subparsers.add_parser("enable", help="Enable a plugin by id.")
        enable_parser.add_argument("plugin_id")

        disable_parser = subparsers.add_parser("disable", help="Disable a plugin by id.")
        disable_parser.add_argument("plugin_id")

    def handle(self, *args, **options) -> None:
        action = options["action"]
        if action == "list":
            self._list()
        elif action == "discover":
            self._discover()
        elif action == "reload":
            self._reload()
        elif action == "enable":
            self._set_enabled(options["plugin_id"], True)
        elif action == "disable":
            self._set_enabled(options["plugin_id"], False)

    def _list(self) -> None:
        records = PluginRecord.objects.all()
        if not records:
            self.stdout.write("No plugins discovered.")
            return
        for record in records:
            status = "enabled" if record.enabled else "disabled"
            line = f"{record.plugin_id}\t{record.name}\t{record.version}\t{status}"
            if record.error:
                line += f"\tERROR: {record.error}"
            self.stdout.write(line)

    def _discover(self) -> None:
        results = registry.discover_and_sync()
        ok = sum(1 for result in results if result.ok)
        failed = len(results) - ok
        self.stdout.write(self.style.SUCCESS(f"Discovered {len(results)} plugin(s): {ok} ok, {failed} failed."))
        for result in results:
            if not result.ok:
                self.stdout.write(self.style.WARNING(f"  {result.plugin_id}: {result.error}"))

    def _set_enabled(self, plugin_id: str, enabled: bool) -> None:
        try:
            PluginRecord.objects.get(plugin_id=plugin_id)
        except PluginRecord.DoesNotExist:
            raise CommandError(f"Unknown plugin id '{plugin_id}'.")
        try:
            manage_plugin(plugin_id, "load" if enabled else "unload")
        except PluginActivationError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(f"Plugin '{plugin_id}' {'enabled' if enabled else 'disabled'}."))

    def _reload(self) -> None:
        self.stdout.write("Reloading plugins...")
        results = reload_plugins()
        ok = sum(1 for result in results if result.ok)
        failed = len(results) - ok
        self.stdout.write(self.style.SUCCESS(f"Discovered {len(results)} plugin(s): {ok} ok, {failed} failed."))
        for result in results:
            if not result.ok:
                self.stdout.write(self.style.WARNING(f"  {result.plugin_id}: {result.error}"))
