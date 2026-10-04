"""Resource roots for browser-test tools, without importing a web framework."""
from pathlib import Path
from apps.plugin_manager.resource_registry import ResourceRegistry


def static_directories(project_root):
    registry = ResourceRegistry.from_builtins(project_root, {
        "bim_model_manager": "bim_model_manager:plugin_manifest",
        "example_plugin": "example_plugin:plugin_manifest",
    })
    return [Path(project_root) / "resources/static", *(bundle.root / "static" for bundle in registry.bundles())]
