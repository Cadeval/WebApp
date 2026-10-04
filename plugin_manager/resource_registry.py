"""Framework-independent declarations for trusted, installed plugin resources.

Resource registration is separate from personal workflow activation. Public
templates and assets don't execute a plugin or grant access to its routes.
"""
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re


@dataclass(frozen=True)
class OverviewTemplate:
    """A trusted app's explanatory fragment, independent of any framework."""

    template_name: str
    compatibility: str = "both"


def validate_overview(overview, root, namespace):
    if not isinstance(overview, OverviewTemplate) or not isinstance(overview.compatibility, str) or overview.compatibility not in {"debug", "production", "both"}:
        raise ValueError("Invalid landing overview declaration.")
    name = overview.template_name
    if not isinstance(name, str) or not name or len(name) > 256 or any(character in name for character in "\\*?[]") or any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise ValueError("Invalid landing overview template name.")
    path = PurePosixPath(name)
    prefix = PurePosixPath(namespace)
    if path.is_absolute() or path.as_posix() != name or any(part in {".", ".."} for part in path.parts) or not path.is_relative_to(prefix) or len(path.parts) <= len(prefix.parts) or path.suffix not in {".html", ".jinja2"}:
        raise ValueError("Landing overview must use its owner's template namespace.")
    current = Path(root)
    for part in ("templates", *path.parts):
        current = current / part
        if current.is_symlink():
            raise ValueError("Landing overview templates must not be symlinks.")
    if not current.is_file() or current.stat().st_size > 65536:
        raise ValueError("Landing overview requires a small, owned template file.")
    return current


@dataclass(frozen=True)
class ResourceBundle:
    plugin_id: str
    package: str
    root: Path
    overview: OverviewTemplate | None = None


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
        if bundle.overview is not None:
            validate_overview(bundle.overview, bundle.root, bundle.package.removeprefix("plugins.").replace(".", "/"))
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
            package = "plugins." + module
            root = project_root.joinpath("plugins", *module.split("."))
            declaration = root / "resources.json"
            if not declaration.is_file():
                continue
            ancestors = [root, *root.parents]
            if any(path.is_symlink() for path in ancestors if path != project_root and path.is_relative_to(project_root)) or declaration.is_symlink() or not root.resolve().is_relative_to(project_root / "plugins"):
                raise ValueError("Plugin resources must stay in their source package.")
            if declaration.stat().st_size > 4096:
                raise ValueError("Plugin resource declaration is too large.")
            metadata = json.loads(declaration.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict) or set(metadata) not in ({"version", "plugin_id", "package"}, {"version", "plugin_id", "package", "overview"}) or type(metadata["version"]) is not int or metadata["version"] != 1 or metadata["package"] != package:
                raise ValueError("Invalid plugin resource declaration.")
            overview = None
            if "overview" in metadata:
                overview_metadata = metadata["overview"]
                if not isinstance(overview_metadata, dict) or set(overview_metadata) != {"template", "compatibility"}:
                    raise ValueError("Invalid plugin landing overview declaration.")
                overview = OverviewTemplate(overview_metadata["template"], overview_metadata["compatibility"])
            for directory in ("templates", "static", "resources"):
                child = root / directory
                if child.is_symlink():
                    raise ValueError("Plugin resource directories must not be symlinks.")
            initializer = root / "resources/__init__.py"
            if initializer.is_symlink() or not initializer.is_file():
                raise ValueError("Plugin resources need a model-free adapter namespace.")
            registry.register(ResourceBundle(metadata["plugin_id"], package, root, overview))
        return registry
