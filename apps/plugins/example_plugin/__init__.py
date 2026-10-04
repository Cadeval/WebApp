"""Bundled plugin exposing a sandboxed Rust/WebAssembly IFC editor."""

from apps.plugin_manager.manifest import PluginManifest
from apps.plugin_manager.registry import NavItem, PluginRegistry

EXAMPLE_PLUGIN_ID = "cadevil.example.editor"


def register(registry: PluginRegistry) -> None:
    registry.register_nav_item(
        EXAMPLE_PLUGIN_ID,
        NavItem(
            label="IFC Editor",
            url="/plugins/ifc-editor/",
            icon="fa-cube",
            priority=80,
        ),
    )


def plugin_manifest() -> PluginManifest:
    return PluginManifest(
        id=EXAMPLE_PLUGIN_ID,
        name="Cadevil Rust IFC Editor",
        compatibility="both",
        version="2.0.1",
        uploader="",
        api_version="1.0",
        priority=10,
        register=register,
    )
