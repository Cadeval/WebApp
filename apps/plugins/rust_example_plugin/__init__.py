"""Bundled Snake game powered by a Rust-authored WebAssembly module."""

from apps.plugin_manager.manifest import PluginManifest
from apps.plugin_manager.registry import NavItem, PluginRegistry

RUST_EXAMPLE_PLUGIN_ID = "cadevil.rust-example.editor"


def register(registry: PluginRegistry) -> None:
    registry.register_nav_item(
        RUST_EXAMPLE_PLUGIN_ID,
        NavItem(
            label="Rust Snake",
            url="/plugins/rust-snake/",
            icon="fa-gamepad",
            priority=20,
        ),
    )


def plugin_manifest() -> PluginManifest:
    return PluginManifest(
        id=RUST_EXAMPLE_PLUGIN_ID,
        name="Cadevil Rust Snake Plugin",
        compatibility="both",
        type="WebPlugin",
        version="2.0.0",
        api_version="1.0",
        priority=20,
        register=register,
    )
