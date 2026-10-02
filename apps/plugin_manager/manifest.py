"""Plugin manifest definitions for the cadevil plugin system.

Plugins are discovered via ``importlib.metadata`` entry points registered
under the ``cadevil.plugins`` group (see :mod:`apps.plugin_manager.registry`).
Each entry point must resolve to a :class:`PluginManifest` instance (or a
zero-argument callable returning one).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

# The API version implemented by this host application. Plugins declare the
# API version they were built against on their manifest, and are only
# activated when their major version matches ours.
PLUGIN_API_VERSION: str = "1.0"


class PluginError(Exception):
    """Raised when a discovered plugin cannot be loaded or registered."""


@dataclass(frozen=True)
class PluginManifest:
    """Describes a single plugin discovered via the ``cadevil.plugins`` entry point group."""

    id: str
    name: str = ""
    type: str = ""
    uploader: str = ""
    version: str = "0.0.0"
    api_version: str = PLUGIN_API_VERSION
    priority: int = 100
    # Optional hook invoked with the active PluginRegistry instance so the
    # plugin can register nav items / other extensions. Failures raised here
    # are isolated by the registry and never crash discovery/startup.
    register: Callable[[Any], None] | None = None
    compatibility: str = "both"


def _major_version(version: str) -> int | None:
    try:
        return int(str(version).strip().split(".")[0])
    except (ValueError, IndexError, AttributeError):
        return None


def is_api_version_compatible(
    api_version: str, supported: str = PLUGIN_API_VERSION
) -> bool:
    """Return whether ``api_version`` is compatible with ``supported``.

    Compatibility is determined by comparing the major version component
    only, so a plugin declaring ``"1.4"`` is compatible with a host
    supporting ``"1.0"``, but a plugin declaring ``"2.0"`` is not.
    """
    plugin_major = _major_version(api_version)
    supported_major = _major_version(supported)
    return (
        plugin_major is not None
        and supported_major is not None
        and plugin_major == supported_major
    )
