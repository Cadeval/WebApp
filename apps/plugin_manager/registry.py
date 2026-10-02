"""In-memory registry for the cadevil plugin system.

The registry is responsible for:

* Discovering plugins registered under the ``cadevil.plugins``
  ``importlib.metadata`` entry point group.
* Validating each plugin's manifest (unique id, compatible API version).
* Isolating hook/registration errors so a single misbehaving plugin never
  prevents other plugins - or the host application - from starting.
* Persisting the enabled/error state of each discovered plugin via
  :class:`apps.plugin_manager.models.PluginRecord`.
* Exposing typed extension points (starting with navigation items) so the
  host application can render active plugin contributions.
"""

from __future__ import annotations

import importlib
import logging
import threading
from functools import wraps
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import entry_points
from typing import Any
from urllib.parse import urlsplit, unquote
import re

from django.conf import settings
from django.db import DatabaseError

from apps.plugin_manager.manifest import (
    PLUGIN_API_VERSION,
    PluginError,
    PluginManifest,
    is_api_version_compatible,
)

logger = logging.getLogger(__name__)

# Group under which plugins must register their entry point.
ENTRY_POINT_GROUP: str = "cadevil.plugins"

# Built-in extension point for contributing items to the main navigation menu.
NAV_ITEM_EXTENSION_POINT: str = "nav_item"

# Browser plugins mounted in the configuration editor. Their code runs in a
# module worker; the host only owns the declarative controls and message bridge.
EDITOR_PLUGIN_EXTENSION_POINT: str = "editor_plugin"


@dataclass(frozen=True)
class NavItem:
    """A single navigation menu entry contributed by a plugin."""

    label: str
    url: str
    icon: str = ""
    priority: int = 100
    full_page: bool = False


@dataclass(frozen=True)
class EditorPlugin:
    """A host-managed editor panel backed by an isolated browser worker."""

    id: str
    name: str
    description: str
    worker_url: str
    priority: int = 100
    wasm_url: str = ""
    artifact_type: str = "package"
    content_hash: str = ""
    max_run_ms: int = 2000


@dataclass(frozen=True)
class _ConfiguredEntryPoint:
    """Entry-point compatible adapter for plugins bundled with the application."""

    name: str
    dotted_path: str

    def load(self) -> Any:
        module_path, separator, attribute = self.dotted_path.partition(":")
        module_path = "apps.plugins." + module_path
        if not separator or not module_path or not attribute:
            raise PluginError(
                f"Bundled plugin '{self.name}' must use 'module:attribute' syntax."
            )
        return getattr(import_module(module_path), attribute)


@dataclass(frozen=True)
class ExtensionEntry:
    """An extension value contributed by a plugin, alongside its ordering metadata."""

    plugin_id: str
    value: Any
    priority: int = 100


@dataclass
class DiscoveryResult:
    """Outcome of trying to load and register a single plugin entry point."""

    plugin_id: str
    name: str = ""
    version: str = ""
    api_version: str = ""
    priority: int = 100
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def locked(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


class PluginRegistry:
    """Holds discovered plugin manifests and their contributed extensions."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._record_signature = None
        self._registering_plugin = None
        self._extensions: dict[str, list[ExtensionEntry]] = {}
        self._manifests: dict[str, PluginManifest] = {}

    @locked
    def reset(self) -> None:
        """Clear all discovered manifests and extensions.

        Used before every discovery pass, and by tests to isolate state.
        """
        self._extensions.clear()
        self._manifests.clear()

    @locked
    def manifests(self) -> dict[str, PluginManifest]:
        return dict(self._manifests)

    # -- Generic extension point API ------------------------------------

    @locked
    def register_extension(
        self, point: str, plugin_id: str, value: Any, priority: int = 100
    ) -> None:
        """Register ``value`` as a contribution to ``point`` on behalf of ``plugin_id``."""
        if self._registering_plugin is not None and plugin_id != self._registering_plugin:
            raise PluginError("A registration hook may only contribute for its own plugin id.")
        if not isinstance(priority, int) or isinstance(priority, bool):
            raise PluginError("Contribution priority must be an integer.")
        if point == NAV_ITEM_EXTENSION_POINT:
            if not isinstance(value, NavItem) or not isinstance(value.label, str) or not value.label.strip() or len(value.label) > 255 or not isinstance(value.icon, str) or not isinstance(value.full_page, bool):
                raise PluginError("Invalid navigation contribution.")
            self._local_url(value.url)
        if point == EDITOR_PLUGIN_EXTENSION_POINT:
            if not isinstance(value, EditorPlugin) or value.id != plugin_id or not isinstance(value.name, str) or not value.name.strip() or len(value.name) > 255 or not isinstance(value.description, str) or not isinstance(value.max_run_ms, int) or not 0 < value.max_run_ms <= 30000:
                raise PluginError("Invalid editor contribution.")
            self._local_url(value.worker_url)
            if value.wasm_url:
                self._local_url(value.wasm_url)
            if len(value.description) > 4096:
                raise PluginError("Invalid editor contribution.")
        self._extensions.setdefault(point, []).append(
            ExtensionEntry(plugin_id=plugin_id, value=value, priority=priority)
        )

    @locked
    def get_extensions(self, point: str) -> list[ExtensionEntry]:
        """Return every registered contribution for ``point``, sorted by priority (ascending)."""
        return sorted(self._extensions.get(point, []), key=lambda entry: entry.priority)

    @locked
    def get_active(
        self, point: str, enabled_ids: Iterable[str] | None = None
    ) -> list[Any]:
        """Return the contributed values for ``point`` whose owning plugin is enabled.

        Results are ordered by ascending priority. ``enabled_ids`` can be
        supplied to avoid a database lookup (mostly useful for tests); by
        default the set of enabled plugin ids is read from
        :class:`~plugin_manager.models.PluginRecord`.
        """
        if enabled_ids is None:
            self.refresh_if_changed()
            enabled_ids = self._enabled_plugin_ids()
        enabled = set(enabled_ids)
        return [
            entry.value
            for entry in self.get_extensions(point)
            if entry.plugin_id in enabled
        ]

    # -- Typed convenience wrapper for the navigation extension point ---

    @staticmethod
    def _local_url(url):
        if not isinstance(url, str) or not url.startswith("/") or url.startswith("//") or "\\" in url or any(ord(c) < 32 for c in url):
            raise PluginError("Plugin URLs must be local absolute paths.")
        decoded = unquote(unquote(url))
        if "\\" in decoded or any(ord(c) < 32 for c in decoded):
            raise PluginError("Invalid encoded plugin URL.")
        parsed = urlsplit(decoded)
        if parsed.scheme or parsed.netloc or any(part in {".", ".."} for part in parsed.path.split("/")):
            raise PluginError("Plugin URLs must not contain traversal or an external origin.")

    def register_nav_item(self, plugin_id: str, nav_item: NavItem) -> None:
        if not isinstance(nav_item, NavItem) or not isinstance(nav_item.label, str) or not nav_item.label.strip() or not isinstance(nav_item.priority, int):
            raise PluginError("Invalid navigation contribution.")
        self._local_url(nav_item.url)
        self.register_extension(
            NAV_ITEM_EXTENSION_POINT, plugin_id, nav_item, priority=nav_item.priority
        )

    def register_editor_plugin(
        self, plugin_id: str, editor_plugin: EditorPlugin
    ) -> None:
        if not isinstance(editor_plugin, EditorPlugin) or editor_plugin.id != plugin_id or not isinstance(editor_plugin.priority, int) or not isinstance(editor_plugin.max_run_ms, int) or not 0 < editor_plugin.max_run_ms <= 30000:
            raise PluginError("Invalid editor contribution.")
        self._local_url(editor_plugin.worker_url)
        if editor_plugin.wasm_url:
            self._local_url(editor_plugin.wasm_url)
        self.register_extension(
            EDITOR_PLUGIN_EXTENSION_POINT,
            plugin_id,
            editor_plugin,
            priority=editor_plugin.priority,
        )

    def _enabled_plugin_ids(self) -> set[str]:
        # Imported lazily to avoid AppRegistryNotReady during app startup.
        from apps.plugin_manager.models import PluginRecord

        try:
            return set(
                PluginRecord.objects.filter(enabled=True, error="", source=PluginRecord.Source.PACKAGE).values_list(
                    "plugin_id", flat=True
                )
            )
        except DatabaseError:
            logger.warning(
                "Plugin manager: could not read plugin state from the database "
                "(migrations may be pending); treating all plugins as inactive."
            )
            # TODO: Add admin level migration start-button.
            return set()

    # -- Discovery --------------------------------------------------------

    @staticmethod
    def _supported_api_version() -> str:
        # django.conf.settings is imported lazily so this module can be
        # imported (and unit-tested) without requiring Django settings to be
        # fully configured.
        from django.conf import settings

        return getattr(settings, "PLUGIN_API_VERSION", PLUGIN_API_VERSION)

    @staticmethod
    def _resolve_manifest(loaded: Any) -> PluginManifest:
        if isinstance(loaded, PluginManifest):
            return loaded
        if callable(loaded):
            candidate = loaded()
            if isinstance(candidate, PluginManifest):
                return candidate
        raise PluginError(
            f"Entry point did not resolve to a PluginManifest (got {loaded!r})."
        )

    @locked
    def discover_plugins(
        self, entry_points_iterable: Iterable[Any] | None = None
    ) -> list[DiscoveryResult]:
        """Discover plugins and register their extensions.

        Resets the registry and repopulates it from scratch, so this is safe
        to call repeatedly (app startup, the management command, or tests).
        Every entry point is processed independently: a failure loading one
        plugin's manifest, or an error raised from its ``register`` hook,
        never prevents other plugins from being discovered, and never
        propagates out of this method.
        """
        self.reset()
        results: list[DiscoveryResult] = []
        seen_ids: set[str] = set()

        if entry_points_iterable is None:
            installed_entry_points = list(entry_points(group=ENTRY_POINT_GROUP))
            installed_names = {item.name for item in installed_entry_points}
            bundled_entry_points = [
                _ConfiguredEntryPoint(name=name, dotted_path=dotted_path)
                for name, dotted_path in getattr(
                    settings, "PLUGIN_BUILTINS", {}
                ).items()
                if name not in installed_names
            ]
            entry_points_iterable = [*installed_entry_points, *bundled_entry_points]

        for entry_point in entry_points_iterable:
            entry_point_name = str(getattr(entry_point, "name", "<unknown>"))[:200]
            plugin_id = "rejected:" + entry_point_name
            try:
                loaded = entry_point.load()
                manifest = self._resolve_manifest(loaded)
                candidate_id = manifest.id
                if not isinstance(candidate_id, str) or len(candidate_id) > 180 or not re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)*", candidate_id):
                    raise PluginError("Plugin manifest id must use lowercase letters, numbers, dots, dashes or underscores (maximum 180 characters).")
                plugin_id = candidate_id
                for field, limit in [("name",255),("version",50),("api_version",20),("type",255),("uploader",255)]:
                    value = getattr(manifest, field)
                    if not isinstance(value, str) or len(value) > limit:
                        raise PluginError(f"Invalid manifest {field}.")
                if not isinstance(manifest.priority, int) or isinstance(manifest.priority, bool) or not -(2**31) <= manifest.priority < 2**31:
                    raise PluginError("Manifest priority must be a signed 32-bit integer.")
                if manifest.register is not None and not callable(manifest.register):
                    raise PluginError("Manifest registration hook must be callable.")
                if plugin_id in seen_ids:
                    # Do not reuse the colliding id as the DiscoveryResult's
                    # plugin_id: that would make sync_plugin_records()
                    # overwrite the state of the plugin that already won the
                    # id, using a unique label instead so the rejection is
                    # still visible without clobbering the real record.
                    results.append(
                        DiscoveryResult(
                            plugin_id=f"{plugin_id}:rejected-duplicate:{entry_point_name}"[:255],
                            error=f"Duplicate plugin id '{plugin_id}' (entry point '{entry_point_name}' rejected).",
                        )
                    )
                    continue
                supported_api_version = self._supported_api_version()
                if not is_api_version_compatible(
                    manifest.api_version, supported=supported_api_version
                ):
                    raise PluginError(
                        f"Plugin '{plugin_id}' declares API version '{manifest.api_version}', "
                        f"which is incompatible with the supported API version '{supported_api_version}'."
                    )

                seen_ids.add(plugin_id)
                self._manifests[plugin_id] = manifest

                if manifest.register is not None:
                    previous_extensions = {key: list(value) for key, value in self._extensions.items()}
                    try:
                        self._registering_plugin = plugin_id
                        try:
                            manifest.register(self)
                        finally:
                            self._registering_plugin = None
                    except Exception as exc:  # noqa: BLE001 - hook error isolation is intentional
                        self._extensions = previous_extensions
                        self._manifests.pop(plugin_id, None)
                        logger.exception(
                            "Plugin '%s' raised an error while registering hooks.",
                            plugin_id,
                        )
                        results.append(
                            DiscoveryResult(
                                plugin_id=plugin_id,
                                name=manifest.name,
                                version=manifest.version,
                                api_version=manifest.api_version,
                                priority=manifest.priority,
                                error=f"Error while registering hooks: {exc}",
                            )
                        )
                        continue

                results.append(
                    DiscoveryResult(
                        plugin_id=plugin_id,
                        name=manifest.name,
                        version=manifest.version,
                        api_version=manifest.api_version,
                        priority=manifest.priority,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - discovery must never crash startup
                logger.exception("Failed to load plugin entry point '%s'.", plugin_id)
                results.append(DiscoveryResult(plugin_id=plugin_id, error=str(exc)))

        return results

    def sync_plugin_records(self, results: Iterable[DiscoveryResult]) -> None:
        """Persist discovery outcomes to :class:`~apps.plugin_manager.models.PluginRecord`."""
        # Imported lazily to avoid AppRegistryNotReady during app startup.
        from apps.plugin_manager.models import PluginRecord

        results = list(results)
        try:
            PluginRecord.objects.filter(source=PluginRecord.Source.PACKAGE).exclude(
                plugin_id__in=[result.plugin_id for result in results]
            ).update(enabled=False, error="Package is no longer discovered. Restart after installing or removing Python package code.")
            for result in results:
                defaults = {
                    "name": result.name,
                    "version": result.version,
                    "api_version": result.api_version,
                    "priority": result.priority,
                    "error": result.error,
                }
                record, created = PluginRecord.objects.get_or_create(
                    plugin_id=result.plugin_id,
                    defaults={**defaults, "enabled": result.ok},
                )
                if not created:
                    if record.source == PluginRecord.Source.UPLOAD:
                        result.error = "Installed package id collides with an uploaded plugin."
                        logger.error("Installed package id collides with an uploaded plugin: %s", result.plugin_id)
                        self._manifests.pop(result.plugin_id, None)
                        self._extensions = {key: [entry for entry in entries if entry.plugin_id != result.plugin_id] for key, entries in self._extensions.items()}
                        continue
                    for field_name, value in defaults.items():
                        setattr(record, field_name, value)
                    if not result.ok:
                        # A plugin that just failed discovery/validation can
                        # never contribute active extensions, so force it
                        # into a disabled state rather than leaving a
                        # previously-enabled record silently inert.
                        record.enabled = False
                    record.save()
        except DatabaseError:
            logger.warning(
                "Plugin manager: could not persist plugin state to the database "
                "(migrations may be pending)."
            )

    def _package_signature(self):
        from apps.plugin_manager.models import PluginRecord
        try:
            return tuple(PluginRecord.objects.filter(source=PluginRecord.Source.PACKAGE).order_by("plugin_id").values_list("plugin_id", "discovered_at"))
        except DatabaseError:
            return None

    @locked
    def refresh_if_changed(self):
        """Refresh local contributions when another HTTP worker reloads metadata.

        Imported Python code stays process-owned; replacing code requires a restart.
        This pass never writes shared state or overrides administrator toggles.
        """
        signature = self._package_signature()
        if signature is not None and signature != self._record_signature:
            self.discover_plugins()
            self._record_signature = signature

    def reload(self) -> list[DiscoveryResult]:
        """Invalidate import caches and re-discover plugins.

        This is the preferred way to pick up newly installed packages or
        changes to entry points without a process restart.
        """
        importlib.invalidate_caches()
        return self.discover_and_sync()

    @locked
    def discover_and_sync(
        self, entry_points_iterable: Iterable[Any] | None = None
    ) -> list[DiscoveryResult]:
        """Run discovery and immediately persist the resulting state."""
        results = self.discover_plugins(entry_points_iterable)
        self.sync_plugin_records(results)
        self._record_signature = self._package_signature()
        return results


# Process-wide singleton used by the app config, views, management command and context processor.
registry = PluginRegistry()
