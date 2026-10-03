"""BIM workspace contributions to the runtime plugin navigation."""
from apps.plugin_manager.manifest import PluginManifest
from apps.plugin_manager.registry import NavItem, PluginRegistry

PLUGIN_ID = "cadevil.bim.model_manager"

def register(registry: PluginRegistry) -> None:
    for priority, label, url, icon in [
        (80, "BIM Model Manager", "/plugins/bim/model_manager/", "fa-cube"),
        (81, "Reference Configurations", "/plugins/bim/configuration_library/", "fa-table"),
        (82, "Configuration Editor", "/plugins/bim/config_editor/", "fa-pencil"),
        (83, "Material Passport", "/plugins/bim/material-passport/", "fa-recycle"),
        (84, "Model Comparison", "/plugins/bim/material-passport/compare/", "fa-balance-scale"),
    ]:
        registry.register_nav_item(PLUGIN_ID, NavItem(label=label, url=url, icon=icon, priority=priority))

def plugin_manifest() -> PluginManifest:
    return PluginManifest(id=PLUGIN_ID, name="BIM Workspace", compatibility="both", version="1.1.0",
        uploader="", api_version="1.0", priority=10, register=register)
