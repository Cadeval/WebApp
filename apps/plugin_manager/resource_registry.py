"""Framework-independent declarations for trusted, installed plugin resources.

Resource registration is separate from personal workflow activation. Public
templates and assets don't execute a plugin or grant access to its routes.
"""
from dataclasses import dataclass
import json
from pathlib import Path
import re


@dataclass(frozen=True)
class ResourceBundle:
    plugin_id: str
    package: str
    root: Path


class ResourceRegistry:
    def __init__(self):
        self._bundles = {}

    def register(self, bundle):
        if not isinstance(bundle, ResourceBundle):
            raise ValueError("Resources require a ResourceBundle declaration.")
        if not isinstance(bundle.plugin_id, str) or not re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)*", bundle.plugin_id):
            raise ValueError("Invalid resource owner.")
        if not isinstance(bundle.package, str) or not re.fullmatch(r"[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*", bundle.package):
            raise ValueError("Invalid bundled resource package.")
        if bundle.plugin_id in self._bundles or any(value.package == bundle.package for value in self._bundles.values()):
            raise ValueError("Duplicate plugin resource declaration.")
        self._bundles[bundle.plugin_id] = bundle

    def bundles(self):
        return tuple(self._bundles.values())

    @classmethod
    def from_builtins(cls, project_root, configured):
        """Read declarations only from explicitly configured source packages.

        Uploaded browser archives and runtime storage are never searched. No
        plugin factory, hook, model module or framework is imported here.
        """
        registry = cls()
        project_root = Path(project_root).resolve()
        seen = set()
        for entry in configured.values():
            if not isinstance(entry, str):
                raise ValueError("Invalid bundled plugin module.")
            module = entry.partition(":")[0]
            if not re.fullmatch(r"[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*", module):
                raise ValueError("Invalid bundled plugin module.")
            if module in seen:
                continue
            seen.add(module)
            package = "apps.plugins." + module
            root = project_root.joinpath("apps", "plugins", *module.split("."))
            declaration = root / "resources.json"
            if not declaration.is_file():
                continue
            ancestors = [root, *root.parents]
            if any(path.is_symlink() for path in ancestors if path != project_root and path.is_relative_to(project_root)) or declaration.is_symlink() or not root.resolve().is_relative_to(project_root / "apps/plugins"):
                raise ValueError("Plugin resources must stay in their source package.")
            if declaration.stat().st_size > 4096:
                raise ValueError("Plugin resource declaration is too large.")
            metadata = json.loads(declaration.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict) or set(metadata) != {"version", "plugin_id", "package"} or type(metadata["version"]) is not int or metadata["version"] != 1 or metadata["package"] != package:
                raise ValueError("Invalid plugin resource declaration.")
            for directory in ("templates", "static", "resources"):
                child = root / directory
                if child.is_symlink():
                    raise ValueError("Plugin resource directories must not be symlinks.")
            initializer = root / "resources/__init__.py"
            if initializer.is_symlink() or not initializer.is_file():
                raise ValueError("Plugin resources need a model-free adapter namespace.")
            registry.register(ResourceBundle(metadata["plugin_id"], package, root))
        return registry
