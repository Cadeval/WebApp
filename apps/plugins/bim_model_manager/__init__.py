"""BIM workspace contributions to the runtime plugin navigation."""
from apps.plugin_manager.manifest import PluginManifest
from apps.plugin_manager.registry import NavItem, PluginRegistry

PLUGIN_ID = "cadevil.bim.model_manager"

def register(registry: PluginRegistry) -> None:
    registry.register_nav_item(PLUGIN_ID,
        NavItem(label="BIM Workspace", url="/plugins/bim/", icon="fa-cube", priority=80))

def plugin_manifest() -> PluginManifest:
    return PluginManifest(id=PLUGIN_ID, name="BIM Workspace", compatibility="both", version="2.0.1",
        uploader="", api_version="1.0", priority=10, register=register)
