"""Check reviewed build inputs without importing or changing the application."""
import argparse
import ast
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import sys
import tomllib
import types

# The CLI is also safe when invoked without Python's recommended -B option.
sys.dont_write_bytecode = True

MANIFEST = "docker/image-files.json"
BUILD_CONTROLS = {"Dockerfile", ".dockerignore"}
OLD_SHARED_FILES = {
    "assessment_logging.py", "assessment_presentation.py", "assessment_web.py",
    "building_locations.py", "building_thumbnails.py", "cityjson_export.py",
    "cityjson_import.py", "conversion_signals.py", "development_mcp.py",
    "ifc_viewer.py", "location_energy.py", "location_lookup.py", "location_planning.py",
    "location_utilities.py", "model_choice_widgets.py", "viewer_materials.py",
}
OLD_SHARED_DIRECTORIES = {"ifc_extractor", "cityjson_schemas", "location_data"}
LEGACY_BIM_MODELS = {
    "ConfigUpload", "EpwUpload", "CalculationConfig", "FileUpload", "ModelConversion",
    "BuildingLocation", "CadevilDocument", "BuildingMetrics", "MaterialProperties",
}
BIM_IMPORT_PREFIXES = ("plugins.bim_model_manager", "apps.plugins.bim_model_manager")
FRESH_MIGRATION_PREFIXES = (
    "shared/migrations/", "plugins/bim_model_manager/django/migrations/",
)
REQUIRED_RUNTIME = {
    "plugin_manager/resource_registry.py", "plugin_manager/django_resources.py",
    "plugins/bim_model_manager/django/apps.py", "plugins/bim_model_manager/django/models.py",
    "plugins/bim_model_manager/django/migrations/0001_initial.py",
    "plugins/bim_model_manager/resources.json", "plugins/bim_model_manager/resources/__init__.py",
    "shared/models.py", "shared/migrations/0001_initial.py", "config/api.py",
}


def _load_helper(relative):
    """Load only these checked-in, stdlib helpers; never create bytecode caches."""
    if relative not in {"scripts/check_build_artifacts.py", "docker/context.py"}:
        raise ValueError("Unreviewed source-check helper: " + relative)
    path = Path(__file__).absolute().parents[1]
    for part in PurePosixPath(relative).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("Symlink in source-check helper: " + relative)
    if not path.is_file():
        raise ValueError("Missing/nonregular source-check helper: " + relative)
    module = types.ModuleType("cadevil_source_" + path.stem)
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


def _json(content):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key: " + key)
            result[key] = value
        return result

    def finite(value):
        raise ValueError("Nonfinite JSON value: " + value)

    return json.loads(content, object_pairs_hook=unique, parse_constant=finite)


def validate_sbom_graph(document):
    """Require a unique component graph, with explicit entries for leaf nodes."""
    if document.get("bomFormat") != "CycloneDX" or document.get("specVersion") != "1.6":
        raise ValueError("Source SBOM must be the reviewed CycloneDX 1.6 inventory")
    components = document.get("components")
    dependencies = document.get("dependencies")
    if not isinstance(components, list) or not isinstance(dependencies, list):
        raise ValueError("Source SBOM components and dependencies must be lists")
    nodes = [document.get("metadata", {}).get("component", {})]

    def collect(values):
        for value in values:
            if not isinstance(value, dict):
                raise ValueError("Invalid source SBOM component")
            nodes.append(value)
            children = value.get("components", [])
            if not isinstance(children, list):
                raise ValueError("Invalid nested source SBOM components")
            collect(children)

    collect(components)
    refs = [value.get("bom-ref") for value in nodes]
    if any(not isinstance(ref, str) or not ref.strip() for ref in refs) or len(set(refs)) != len(refs):
        raise ValueError("Source SBOM component references are missing or duplicated")
    expected = set(refs)
    seen = set()
    edges = 0
    for entry in dependencies:
        if not isinstance(entry, dict):
            raise ValueError("Invalid source SBOM dependency entry")
        ref = entry.get("ref")
        if not isinstance(ref, str) or ref not in expected or ref in seen:
            raise ValueError("Source SBOM dependency references are unknown or duplicated")
        seen.add(ref)
        for field in ("dependsOn", "provides"):
            targets = entry.get(field, [])
            if not isinstance(targets, list) or any(not isinstance(target, str) for target in targets):
                raise ValueError("Invalid source SBOM dependency targets")
            if len(set(targets)) != len(targets) or any(target not in expected for target in targets):
                raise ValueError("Source SBOM dependency targets are dangling or duplicated")
            edges += len(targets)
    if seen != expected:
        raise ValueError("Source SBOM graph lacks explicit dependency entries for all components")
    return {"sbom_component_count": len(nodes), "sbom_dependency_edge_count": edges}


def validate_compiler_copy_layout(root, manifest):
    """Prove each received input reaches its unchanged path before uv build."""
    root = Path(root)
    inputs = set(manifest["runtime"] + manifest["build_only"])
    expected = inputs - BUILD_CONTROLS
    instructions = re.sub(r"\\\r?\n", " ", (root / "Dockerfile").read_text()).splitlines()
    stages = set()
    stage = None
    workdir = None
    builder_count = 0
    gate_seen = False
    copied = set()
    for instruction in instructions:
        words = shlex.split(instruction, comments=True)
        if not words:
            continue
        command, args = words[0].upper(), words[1:]
        if command == "ADD":
            raise ValueError("Docker ADD is unsupported; use finite reviewed COPY inputs")
        if command == "FROM":
            stage = args[-1] if len(args) >= 3 and args[-2].upper() == "AS" else None
            workdir = None
            if stage is not None:
                if stage in stages:
                    raise ValueError("Duplicate Docker stage name: " + stage)
                stages.add(stage)
            if stage == "builder":
                builder_count += 1
            continue
        if command == "WORKDIR" and stage == "builder":
            if len(args) != 1:
                raise ValueError("Unsupported compiler WORKDIR")
            workdir = args[0]
            continue
        if command == "RUN" and stage == "builder":
            builds = any(word.rsplit("/", 1)[-1] == "uv" and args[index + 1:index + 2] == ["build"]
                         for index, word in enumerate(args))
            if builds or "--received-context" in args:
                gate_seen = True
            continue
        if command != "COPY":
            continue
        if len(args) < 2 or any("$" in value for value in args):
            raise ValueError("Docker COPY must use literal sources and destinations")
        flags = [value for value in args if value.startswith("--")]
        if flags:
            if len(flags) != 1 or args[0] != flags[0] or not flags[0].startswith("--from="):
                raise ValueError("Unsupported Docker COPY flags")
            origin = flags[0].removeprefix("--from=")
            if not origin or origin not in stages or origin == stage or len(args) < 3:
                raise ValueError("Docker COPY must reference an earlier declared stage")
            for value in args[1:]:
                canonical = value.rstrip("/") or "/"
                path = PurePosixPath(canonical)
                if not path.is_absolute() or path.as_posix() != canonical or value.startswith("//") or value not in {canonical, canonical + "/"} or any(part in {".", ".."} for part in path.parts) or any(character in value for character in "\\*?[]") or any(ord(character) < 32 or ord(character) == 127 for character in value):
                    raise ValueError("Stage COPY requires canonical absolute paths")
            if "/" in args[1:-1]:
                raise ValueError("Broad root stage COPY is unsupported")
            if stage == "builder" and (not args[-1].startswith("/") or args[-1] == "/build" or args[-1].startswith("/build/")):
                raise ValueError("Unreviewed stage COPY into received compiler source")
            continue
        if stage != "builder" or gate_seen or workdir != "/build":
            raise ValueError("Source COPY must occur in /build before the compiler uv build gate")
        destination = args[-1].removeprefix("./")
        if destination and destination != ".":
            path = PurePosixPath(destination.rstrip("/"))
            if path.is_absolute() or path.as_posix() != destination.rstrip("/") or any(part in {".", ".."} for part in path.parts) or any(value in destination for value in "\\*?[]"):
                raise ValueError("Noncanonical compiler COPY destination")
        else:
            destination = ""
        for source in args[:-1]:
            if source.endswith("/"):
                prefix = source[:-1]
                path = PurePosixPath(prefix)
                if not prefix or path.is_absolute() or path.as_posix() != prefix or any(part in {".", ".."} for part in path.parts) or any(value in source for value in "\\*?[]"):
                    raise ValueError("Broad or noncanonical compiler COPY source")
                matches = {name for name in inputs if name.startswith(source)}
                if not matches or (destination and not destination.endswith("/")):
                    raise ValueError("Compiler directory COPY must preserve its reviewed directory")
                mappings = {name: destination + name[len(source):] for name in matches}
            else:
                if source not in expected:
                    raise ValueError("Unreviewed or broad compiler COPY source: " + source)
                target = destination + PurePosixPath(source).name if not destination or destination.endswith("/") else destination
                mappings = {source: target}
            for source_name, target in mappings.items():
                if target != source_name:
                    raise ValueError("Compiler COPY changes reviewed input path: " + source_name)
                if source_name in copied:
                    raise ValueError("Compiler COPY duplicates reviewed input: " + source_name)
                copied.add(source_name)
    if builder_count != 1 or not gate_seen:
        raise ValueError("Dockerfile requires one builder stage and a uv build source-check gate")
    if copied != expected:
        raise ValueError("Compiler COPY omits reviewed inputs: " + ", ".join(sorted(expected - copied)))
    return {"compiler_copy_input_count": len(copied), "compiler_copy_layout": "passed"}


def validate_bim_isolation(root, manifest, *, parsed_trees=None):
    """Keep BIM implementation and fresh model history in its owning plugin."""
    runtime = manifest["runtime"]
    missing = REQUIRED_RUNTIME - set(runtime)
    if missing:
        raise ValueError("Required plugin-owned runtime inputs omitted: " + ", ".join(sorted(missing)))
    inspected = 0
    migrations = 0
    legacy_names = {name.lower() for name in LEGACY_BIM_MODELS}
    for value in runtime:
        path = PurePosixPath(value)
        if value.startswith("shared/"):
            relative = path.relative_to("shared")
            if relative.parts[0] in OLD_SHARED_DIRECTORIES or (len(relative.parts) == 1 and relative.name in OLD_SHARED_FILES):
                raise ValueError("BIM/provider implementation remains in shared runtime: " + value)
            if path.suffix == ".py":
                inspected += 1
                tree = parsed_trees[value] if parsed_trees is not None else ast.parse((Path(root) / value).read_text(encoding="utf-8"), filename=value)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(BIM_IMPORT_PREFIXES):
                        raise ValueError("Shared runtime imports plugin-owned BIM implementation: " + value)
                    if isinstance(node, ast.Import) and any(alias.name.startswith(BIM_IMPORT_PREFIXES) for alias in node.names):
                        raise ValueError("Shared runtime imports plugin-owned BIM implementation: " + value)
                    if isinstance(node, ast.ClassDef) and node.name in LEGACY_BIM_MODELS:
                        raise ValueError("Shared runtime declares a legacy BIM model: " + value)
                    if isinstance(node, ast.Constant) and isinstance(node.value, str):
                        if node.value.rsplit(".", 1)[-1].lower() in legacy_names:
                            raise ValueError("Shared runtime contains a legacy BIM model reference: " + value)
                        if node.value.startswith(BIM_IMPORT_PREFIXES):
                            raise ValueError("Shared runtime contains a BIM compatibility forwarding path: " + value)
        if value == "plugins/bim_model_manager/models.py" or value.startswith("plugins/bim_model_manager/migrations/"):
            raise ValueError("Legacy BIM model/history namespace remains in runtime: " + value)
        if value.startswith(FRESH_MIGRATION_PREFIXES) and path.suffix == ".py":
            if path.name not in {"__init__.py", "0001_initial.py"}:
                raise ValueError("Historical application migration remains in clean-break runtime: " + value)
            migrations += 1
    return {"shared_python_files_inspected": inspected, "fresh_application_migration_file_count": migrations,
            "plugin_owned_bim_isolation": "passed"}


def validate_source(root, *, received=False):
    """Validate a checkout or the exact source received by the compiler stage."""
    root = Path(root).resolve()
    checks = _load_helper("scripts/check_build_artifacts.py")
    context = _load_helper("docker/context.py")
    # Parse once strictly before using the shared manifest primitives.
    _json(checks.regular_file(root, MANIFEST).read_bytes())
    manifest = checks.reviewed_manifest(root)
    if manifest != context.validate(root, received=received):
        raise ValueError("Source/context manifest policies disagree")
    if any("mcp_tools" in PurePosixPath(value).parts for group in ("runtime", "build_only") for value in manifest[group]):
        raise ValueError("Development tool environments cannot be reviewed source inputs")
    project = tomllib.loads(checks.regular_file(root, "pyproject.toml").read_text())["project"]
    parsed_trees = {
        value: ast.parse(checks.regular_file(root, value).read_text(encoding="utf-8"), filename=value)
        for value in manifest["runtime"] if PurePosixPath(value).suffix == ".py"
    }
    bom = _json(checks.regular_file(root, "sbom/cadevil.cdx.json").read_bytes())
    checks.validate_source_sbom(bom, root, project)
    result = {
        "status": "passed", "project": project["name"], "release": project["version"],
        "received_context": bool(received),
        "reviewed_input_count": len(manifest["runtime"]) + len(manifest["build_only"]),
        "runtime_input_count": len(manifest["runtime"]), "build_only_input_count": len(manifest["build_only"]),
        "runtime_python_files_parsed": len(parsed_trees),
        "reviewed_manifest_sha256": sha256(checks.regular_file(root, MANIFEST).read_bytes()).hexdigest(),
        "source_sbom_inputs": "passed", "context_allowlist": "passed",
    }
    result.update(validate_sbom_graph(bom))
    result.update(validate_bim_isolation(root, manifest, parsed_trees=parsed_trees))
    result.update({"compiler_copy_layout": "checked_before_transmission"} if received else validate_compiler_copy_layout(root, manifest))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--received", action="store_true", help="Check the exact compiler-stage source, without Docker build controls")
    args = parser.parse_args()
    try:
        report = validate_source(args.source, received=args.received)
    except SyntaxError as error:
        parser.exit(1, f"Source check failed: {error.filename}:{error.lineno}:{error.offset}: {error.msg}\n")
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, "Source check failed: " + str(error) + "\n")
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
