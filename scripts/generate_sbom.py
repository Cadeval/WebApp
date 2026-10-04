#!/usr/bin/env python3
"""Export locked dependencies and local asset evidence as CycloneDX 1.6.

No network requests, application imports, environment paths or user data enter
the BOM. uv performs the Python lock selection; the merger retains its graph
and every locked archive's URL and hash. See docs/SBOM.md for the boundaries.
"""

from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from hashlib import sha256
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
from urllib.parse import quote

from cyclonedx.schema import SchemaVersion
from cyclonedx.validation.json import JsonStrictValidator
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packageurl import PackageURL


ROOT = Path(__file__).resolve().parents[1]
SBOM = ROOT / "sbom"
INPUTS = SBOM / "inputs"
UV_VERSION = "0.12.22"
VALIDATOR_VERSION = "11.12.0"
JSONSCHEMA_VERSION = "4.26.0"
GENERATOR_VERSION = "1.1.0"


def serialized(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def properties(values: dict[str, str]) -> list[dict[str, str]]:
    return [{"name": key, "value": value} for key, value in sorted(values.items())]


def purl(kind: str, name: str, version: str) -> str:
    if kind == "pypi":
        name = canonicalize_name(name)
    return f"pkg:{kind}/{quote(name, safe='/')}@{quote(version, safe='')}"


def checked_run(arguments: list[str]) -> str:
    environment = dict(os.environ)
    environment.setdefault("UV_CACHE_DIR", str(ROOT / ".venv" / "sbom-uv-cache"))
    result = subprocess.run(arguments, cwd=ROOT, env=environment, capture_output=True, text=True, check=True)
    return result.stdout


def check_tool_versions(uv: str) -> None:
    version = checked_run([uv, "--version"]).split()[1]
    if version != UV_VERSION:
        raise ValueError(f"SBOM export requires uv {UV_VERSION}; found {version}. Run make sbom-setup.")
    version = importlib.metadata.version("cyclonedx-python-lib")
    if version != VALIDATOR_VERSION:
        raise ValueError(f"SBOM validation requires cyclonedx-python-lib {VALIDATOR_VERSION}; found {version}.")
    version = importlib.metadata.version("jsonschema")
    if version != JSONSCHEMA_VERSION:
        raise ValueError(f"SBOM validation requires jsonschema {JSONSCHEMA_VERSION}; found {version}.")


def license_metadata(distribution: importlib.metadata.Distribution) -> dict:
    metadata = distribution.metadata
    result = {"metadataSha256": sha256(distribution.read_text("METADATA").encode()).hexdigest()}
    expression = metadata.get("License-Expression")
    name = metadata.get("License", "").strip()
    classifiers = [entry.removeprefix("License :: ") for entry in metadata.get_all("Classifier", []) if entry.startswith("License :: ")]
    if expression:
        result["license"] = {"expression": expression}
        result["evidence"] = "distribution METADATA License-Expression"
    elif name:
        # Keep a declared name as a name, never infer SPDX IDs from prose.
        result["license"] = {"license": {"name": name.splitlines()[0]}}
        result["evidence"] = "distribution METADATA License (first line; full METADATA hash retained)"
    elif classifiers:
        result["license"] = {"license": {"name": "; ".join(classifiers)}}
        result["evidence"] = "distribution METADATA license classifiers; SPDX expression unverified"
    else:
        result["evidence"] = "distribution METADATA has no license declaration"
    return result


def installed_snapshot(path: Path) -> list[dict]:
    sites = sorted(path.glob("lib/python*/site-packages"))
    if len(sites) != 1:
        raise ValueError(f"Expected one installed Python environment at {path}.")
    records = []
    for distribution in importlib.metadata.distributions(path=[str(sites[0])]):
        metadata = distribution.metadata
        records.append({
            "name": canonicalize_name(metadata["Name"]),
            "version": distribution.version,
            "requiresDist": sorted(metadata.get_all("Requires-Dist", [])),
            **license_metadata(distribution),
        })
    return sorted(records, key=lambda record: (record["name"], record["version"]))


def capture_inputs(tool_root: Path, application_env: Path) -> None:
    """Explicitly refresh public dependency evidence, never copy environment paths."""
    INPUTS.mkdir(parents=True, exist_ok=True)
    environments = {
        "git-mcp": tool_root / "python/mcp-server-git",
        "code-audit": tool_root / "code-audit/.venv",
        "dependency-audit": tool_root / "dependency-audit/.venv",
        "docker": tool_root / "docker/.venv",
        "sbom-generator": Path(sys.prefix),
    }
    snapshot = {"formatVersion": 1, "environments": {name: installed_snapshot(path) for name, path in environments.items()}}
    (INPUTS / "python-tools.json").write_text(serialized(snapshot))
    licenses = {}
    for records in [installed_snapshot(application_env), *snapshot["environments"].values()]:
        for record in records:
            ref = purl("pypi", record["name"], record["version"])
            licenses.setdefault(ref, {key: value for key, value in record.items() if key in {"license", "metadataSha256", "evidence"}})
    (INPUTS / "python-license-evidence.json").write_text(serialized(licenses))
    for name in ["context7", "ui-ux-suite"]:
        (INPUTS / f"{name}.package-lock.json").write_bytes((tool_root / name / "package-lock.json").read_bytes())
    for name in ["code-audit", "dependency-audit", "docker"]:
        (INPUTS / f"{name}.requirements.lock").write_bytes((tool_root / name / "requirements.lock").read_bytes())
    provenance = json.loads((tool_root / "code-audit/provenance.json").read_text())
    safe_fields = ["semgrep", "ruff", "semgrep_rules_commit", "semgrep_rules_archive_url", "semgrep_rules_archive_sha256", "requirements_lock_sha256"]
    (INPUTS / "code-audit-provenance.json").write_text(serialized({key: provenance[key] for key in safe_fields}))
    docker = json.loads((tool_root / "docker/provenance.json").read_text())
    docker_fields = ["provider", "version", "license", "repository", "pypi",
                     "wheel_sha256", "requirements_sha256", "installed_packages",
                     "production_dependencies_added"]
    if docker["requirements_sha256"] != digest(tool_root / "docker/requirements.lock"):
        raise ValueError("Docker development lock differs from its reviewed provenance.")
    (INPUTS / "docker-provenance.json").write_text(serialized({key: docker[key] for key in docker_fields}))


class Inventory:
    def __init__(self, name: str, version: str, description: str):
        self.component = {
            "type": "application", "bom-ref": purl("generic", name, version),
            "name": name, "version": version, "description": description,
            "licenses": [{"license": {"id": "MIT"}}],
        }
        self.components: dict[str, dict] = {}
        self.dependencies: dict[str, set[str]] = {self.component["bom-ref"]: set()}
        self.evidence: dict[str, str] = {}

    def add(self, component: dict, depends_on: list[str] = ()) -> str:
        ref = component["bom-ref"]
        existing = self.components.get(ref)
        if existing:
            for key in ["properties", "externalReferences", "hashes"]:
                values = existing.get(key, []) + component.get(key, [])
                unique = {json.dumps(value, sort_keys=True): value for value in values}
                if unique:
                    existing[key] = [unique[key] for key in sorted(unique)]
        else:
            self.components[ref] = deepcopy(component)
        self.dependencies.setdefault(ref, set()).update(depends_on)
        return ref

    def evidence_file(self, path: Path) -> None:
        self.evidence[path.relative_to(ROOT).as_posix()] = digest(path)

    def output(self, validate: bool = True) -> dict:
        self.evidence_file(ROOT / "scripts/generate_sbom.py")
        self.evidence_file(ROOT / "LICENSE")
        value = {
            "$schema": "http://cyclonedx.org/schema/bom-1.6.schema.json",
            "bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1,
            "metadata": {
                "component": self.component,
                "tools": {"components": [
                    {"type": "application", "name": "uv", "version": UV_VERSION},
                    {"type": "library", "name": "cyclonedx-python-lib", "version": VALIDATOR_VERSION},
                    {"type": "application", "name": "cadevil-sbom-merger", "version": GENERATOR_VERSION},
                ]},
                "properties": properties({
                    "cadevil:inventory:scope": "locked application dependencies and declared assets; see docs/SBOM.md",
                    "cadevil:inventory:completeness": "incomplete: operating system, embedded native libraries and externally uploaded plugins excluded",
                    **{f"cadevil:input:sha256:{name}": value for name, value in self.evidence.items()},
                }),
            },
            "components": [self.components[key] for key in sorted(self.components)],
            "dependencies": [{"ref": key, "dependsOn": sorted(self.dependencies[key])} for key in sorted(self.dependencies)],
            "compositions": [{"aggregate": "incomplete", "assemblies": [self.component["bom-ref"]]}],
        }
        validate_graph(value)
        if validate:
            errors = JsonStrictValidator(SchemaVersion.V1_6).validate_str(serialized(value), all_errors=True)
            if errors:
                raise ValueError(f"CycloneDX schema validation failed: {list(errors)}")
        return value


def validate_graph(value: dict) -> None:
    components = value["components"]
    for component in components:
        if component.get("purl"):
            PackageURL.from_string(component["purl"])
    refs = [value["metadata"]["component"]["bom-ref"], *(component["bom-ref"] for component in components)]
    if len(refs) != len(set(refs)):
        raise ValueError("Duplicate component references")
    dependency_refs = {dependency["ref"] for dependency in value["dependencies"]}
    if dependency_refs != set(refs):
        raise ValueError("Incomplete graph: every component needs an explicit dependency entry")
    if any(target not in dependency_refs for dependency in value["dependencies"] for target in dependency.get("dependsOn", [])):
        raise ValueError("Dangling dependency reference")


def enrich_license(component: dict, licenses: dict) -> None:
    record = licenses.get(component["bom-ref"])
    entries = {entry["name"]: entry["value"] for entry in component.get("properties", [])}
    if record:
        if record.get("license"):
            component["licenses"] = [record["license"]]
        entries.update({"cadevil:license:evidence": record["evidence"], "cadevil:evidence:metadata-sha256": record["metadataSha256"]})
    else:
        entries["cadevil:license:evidence"] = "not declared in lockfile; no matching captured distribution METADATA"
    component["properties"] = properties(entries)


def python_lock_coverage(lock: dict, development: bool) -> set[tuple[str, str]]:
    """Independently check uv's selected package closure, including extras.

    Markers are retained as evidence, not evaluated: a source inventory covers
    every locked platform alternative, unlike an installed-environment BOM.
    """
    roots = [package for package in lock["package"]
             if any(package.get("source", {}).get(kind) == "." for kind in ("virtual", "editable"))]
    if len(roots) != 1:
        raise ValueError("The lock must contain exactly one local project root.")
    root = roots[0]
    packages: dict[str, list[dict]] = {}
    for package in lock["package"]:
        packages.setdefault(package["name"], []).append(package)
    pending = list(root.get("dependencies", []))
    if development:
        for group in root.get("dev-dependencies", {}).values():
            pending.extend(group)
    selected = set()
    visited = set()
    while pending:
        dependency = pending.pop()
        candidates = [package for package in packages[dependency["name"]] if (not dependency.get("version") or package["version"] == dependency["version"]) and (not dependency.get("source") or package["source"] == dependency["source"])]
        extras = dependency.get("extra", [])
        if isinstance(extras, str):
            extras = [extras]
        for package in candidates:
            key = (package["name"], package["version"])
            state = (*key, tuple(sorted(extras)))
            selected.add(key)
            if state in visited:
                continue
            visited.add(state)
            pending.extend(package.get("dependencies", []))
            for extra in extras:
                pending.extend(package.get("optional-dependencies", {}).get(extra, []))
    return selected


def python_export(inventory: Inventory, uv: str, licenses: dict, development: bool) -> None:
    command = [uv, "export", "--locked", "--offline", "--preview-features", "sbom-export", "--format", "cyclonedx1.5"]
    command.append("--all-groups" if development else "--no-dev")
    exported = json.loads(checked_run(command))
    mapping = {exported["metadata"]["component"]["bom-ref"]: inventory.component["bom-ref"]}
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    actual = {(component["name"], component["version"]) for component in exported["components"]}
    expected = python_lock_coverage(lock, development)
    if actual != expected:
        raise ValueError(f"Python lock coverage mismatch: missing {expected - actual}; unexpected {actual - expected}")
    locked = {(package["name"], package["version"]): package for package in lock["package"]}
    for component in exported["components"]:
        mapping[component["bom-ref"]] = component["purl"]
    for component in exported["components"]:
        component["bom-ref"] = component["purl"]
        component["scope"] = "optional" if development else "required"
        package = locked[(component["name"], component["version"])]
        entries = {entry["name"]: entry["value"] for entry in component.get("properties", [])}
        entries.update({"cadevil:evidence:source": "uv.lock via pinned uv export", "cadevil:inventory:selection": "union of locked platform variants; installation varies by markers"})
        if package.get("dependencies") or package.get("optional-dependencies"):
            entries["cadevil:python:conditional-dependencies"] = json.dumps({"dependencies": package.get("dependencies", []), "optional-dependencies": package.get("optional-dependencies", {})}, sort_keys=True)
        component["properties"] = properties(entries)
        enrich_license(component, licenses)
        inventory.add(component)
    for dependency in exported["dependencies"]:
        inventory.dependencies[mapping[dependency["ref"]]].update(mapping[ref] for ref in dependency.get("dependsOn", []))
    inventory.evidence_file(ROOT / "pyproject.toml")
    inventory.evidence_file(ROOT / "uv.lock")


def npm_inventory(inventory: Inventory, path: Path, development: bool, container_name: str | None = None,
                  *, root_packages: set[str] | None = None, usage: str | None = None) -> None:
    lock = json.loads(path.read_text())
    records = lock["packages"]

    def declared(name):
        record = records[name]
        dependencies = {**record.get("dependencies", {}), **record.get("optionalDependencies", {}),
                        **record.get("peerDependencies", {}), **(record.get("devDependencies", {}) if not name else {})}
        if not name and root_packages is not None:
            if not root_packages <= dependencies.keys():
                raise ValueError("The selected npm runtime roots are absent from their lockfile.")
            dependencies = {key:value for key,value in dependencies.items() if key in root_packages}
        optional = set(record.get("optionalDependencies", {})) | {key for key,value in record.get("peerDependenciesMeta", {}).items() if value.get("optional")}
        return dependencies, optional

    def resolve(name, dependency_name, optional):
        parts = name.split("/") if name else []
        while True:
            candidate = "/".join([*parts, "node_modules", dependency_name])
            if candidate in records:
                return candidate
            if not parts:
                if dependency_name in optional:
                    return None
                raise ValueError(f"Unresolved npm dependency {dependency_name} in {path.name}")
            parts.pop()

    # A runtime subset follows the parser roots, excluding the bundler and its
    # optional platform binaries. The development inventory retains the full lock.
    selected = set(records) if root_packages is None else {""}
    if root_packages is not None:
        pending = [""]
        while pending:
            name = pending.pop()
            dependencies, optional = declared(name)
            for dependency_name in dependencies:
                candidate = resolve(name, dependency_name, optional)
                if candidate is not None and candidate not in selected:
                    selected.add(candidate)
                    pending.append(candidate)
    mapping = {}
    root_ref = inventory.component["bom-ref"]
    if container_name:
        container = {"type": "application", "bom-ref": purl("generic", container_name, records[""].get("version", "snapshot")), "name": container_name, "version": records[""].get("version", "snapshot"), "scope": "optional"}
        root_ref = inventory.add(container)
        inventory.dependencies[inventory.component["bom-ref"]].add(root_ref)
    mapping[""] = root_ref
    for name, record in records.items():
        if not name or name not in selected:
            continue
        package_name = record.get("name", name.rsplit("node_modules/", 1)[1])
        ref = purl("npm", package_name, record["version"])
        mapping[name] = ref
        component = {
            "type": "library", "bom-ref": ref, "purl": ref,
            "name": package_name, "version": record["version"],
            "scope": "optional" if development else "required",
            "properties": properties({
                "cadevil:evidence:source": path.relative_to(ROOT).as_posix(),
                "cadevil:inventory:usage": usage or ("development tool" if development else "browser runtime CDN; npm devDependency is used by Node tests"),
            }),
        }
        if record.get("license"):
            component["licenses"] = [{"expression": record["license"]}]
        reference = {"type": "distribution", "url": record["resolved"]} if record.get("resolved") else None
        if reference and record.get("integrity"):
            algorithm, encoded = record["integrity"].split("-", 1)
            reference["hashes"] = [{"alg": {"sha1": "SHA-1", "sha256": "SHA-256", "sha384": "SHA-384", "sha512": "SHA-512"}[algorithm], "content": base64.b64decode(encoded).hex()}]
        if reference:
            component["externalReferences"] = [reference]
        inventory.add(component)
    for name, record in records.items():
        if name not in selected:
            continue
        dependencies, optional = declared(name)
        for dependency_name in dependencies:
            candidate = resolve(name, dependency_name, optional)
            if candidate is not None:
                inventory.dependencies[mapping[name]].add(mapping[candidate])
    # npm may retain orphaned platform/optional entries. Their exact package
    # presence is preserved; no fabricated dependency edge is added.
    inventory.evidence_file(path)


def browser_pki_inventory(inventory: Inventory, *, development: bool) -> None:
    path = ROOT / "scripts/browser-pki/package-lock.json"
    manifest = ROOT / "scripts/browser-pki/package.json"
    expected = {"pkijs":"3.4.1", "es-module-lexer":"3.0.2", "esbuild":"0.28.2"}
    lock = json.loads(path.read_text())
    if json.loads(manifest.read_text()) != {"dependencies":expected} or lock["packages"][""].get("dependencies") != expected:
        raise ValueError("The browser PKI parser/bundler pins differ from their reviewed declarations.")
    for name,version in expected.items():
        if lock["packages"]["node_modules/" + name]["version"] != version:
            raise ValueError("A browser PKI parser/bundler version differs from its exact pin.")
    npm_inventory(inventory, path, development,
                  "cadevil-browser-pki-build-environment" if development else None,
                  root_packages=None if development else {"pkijs", "es-module-lexer"},
                  usage="browser parser dependency closure; tree-shaken modules are not independently attested" if not development else "browser parser bundler and its complete locked platform alternatives")
    inventory.evidence_file(manifest)
    inventory.evidence_file(ROOT / "scripts/vendor_browser_pki.mjs")
    if development:
        return
    license_path = ROOT / "resources/static/js/vendor/browser_pki.js.LICENSE.txt"
    license_text = license_path.read_text()
    packages = {"pkijs", "asn1js", "pvtsutils", "pvutils", "bytestreamjs", "tslib", "@noble/hashes", "es-module-lexer"}
    selected = {component["name"] for component in inventory.components.values()
                if component.get("purl", "").startswith("pkg:npm/") and
                any(value.get("value") == path.relative_to(ROOT).as_posix() for value in component.get("properties", []))}
    if selected != packages:
        raise ValueError("Browser parser dependency closure differs from its eight retained license declarations.")
    for name in packages:
        record = lock["packages"]["node_modules/" + name]
        if f"{name} {record['version']} ({record['license']})\n" not in license_text:
            raise ValueError("A browser parser license/version declaration is missing from its retained license file.")
    for filename in ("resources/static/js/vendor/browser_pki.js", "resources/static/js/vendor/browser_pki.js.LICENSE.txt",
                     "resources/static/js/plugin_verification.js", "resources/static/js/verified_plugin_worker.js",
                     "resources/static/js/plugin_worker_bootstrap.js"):
        ref = "urn:cadevil:asset:" + quote(filename, safe="/")
        inventory.add({"type":"file", "bom-ref":ref, "name":filename, "scope":"required",
                       "hashes":[{"alg":"SHA-256", "content":digest(ROOT / filename)}],
                       "properties":properties({"cadevil:evidence:source":"Tracked vendored parser bytes; package archive integrity does not independently attest the minified bundle."})})
        inventory.dependencies[inventory.component["bom-ref"]].add(ref)
        inventory.dependencies[ref].update(purl("npm", name, expected[name]) for name in ("pkijs","es-module-lexer"))
        inventory.evidence_file(ROOT / filename)


def asset_component(inventory: Inventory, name: str, version: str, files: list[str], source: str, license_entry: dict | None, kind: str = "npm", usage: str = "vendored browser runtime") -> str:
    ref = purl(kind, name, version)
    component = {"type": "library", "bom-ref": ref, "purl": ref, "name": name, "version": version, "scope": "required", "externalReferences": [{"type": "vcs", "url": source}], "properties": properties({"cadevil:inventory:usage": usage, "cadevil:license:evidence": "source/license evidence listed in docs/SBOM.md" if license_entry else "license not verified; review required"})}
    if license_entry:
        component["licenses"] = [license_entry]
    inventory.add(component)
    inventory.dependencies[inventory.component["bom-ref"]].add(ref)
    for filename in files:
        path = ROOT / filename
        asset_ref = f"urn:cadevil:asset:{quote(filename, safe='/')}"
        inventory.add({"type": "file", "bom-ref": asset_ref, "name": filename, "hashes": [{"alg": "SHA-256", "content": digest(path)}], "scope": "required", "properties": properties({"cadevil:evidence:source": "tracked asset bytes; no source/build equivalence claimed"})})
        inventory.dependencies[ref].add(asset_ref)
        inventory.evidence_file(path)
    return ref


def browser_inventory(inventory: Inventory) -> None:
    # Versions must match the checked asset declarations; fail when they drift.
    declarations = [
        ("resources/static/js/htmx.js", "this.version = '4.0.0'"),
        ("resources/static/js/plotly-2.35.3.min.js", "plotly.js v2.35.3"),
        ("resources/static/js/svg-pan-zoom.min.js", "svg-pan-zoom v3.6.2"),
        ("resources/static/vendor/leaflet/leaflet-src.esm.js", 'var version = "1.9.4"'),
        ("resources/static/css/vendor/normalize.css", "normalize.css v8.0.1"),
        ("resources/templates/base.jinja2", "https://cdn.jsdelivr.net/npm/three@0.184.0/build/three.module.js"),
        ("resources/templates/base.jinja2", "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/4.7.0/css/font-awesome.min.css"),
    ]
    for filename, expected in declarations:
        if expected not in (ROOT / filename).read_text():
            raise ValueError(f"Browser inventory version drift: {filename}; update inventory and evidence.")
    asset_component(inventory, "htmx.org", "4.0.0", ["resources/static/js/htmx.js", "resources/static/js/htmx.LICENSE", "resources/static/js/htmx.version.txt"], "https://github.com/bigskysoftware/htmx/tree/v4.0.0", {"license": {"id": "0BSD"}})
    asset_component(inventory, "plotly.js", "2.35.3", ["resources/static/js/plotly-2.35.3.min.js"], "https://github.com/plotly/plotly.js/tree/v2.35.3", {"license": {"id": "MIT"}})
    asset_component(inventory, "svg-pan-zoom", "3.6.2", ["resources/static/js/svg-pan-zoom.min.js"], "https://github.com/bumbu/svg-pan-zoom", None)
    asset_component(inventory, "normalize.css", "8.0.1", ["resources/static/css/vendor/normalize.css", "resources/static/css/vendor/normalize.LICENSE.md"], "https://github.com/necolas/normalize.css/tree/8.0.1", {"license": {"id": "MIT"}})
    leaflet_files = [path.relative_to(ROOT).as_posix() for path in sorted((ROOT / "resources/static/vendor/leaflet").rglob("*")) if path.is_file()]
    asset_component(inventory, "leaflet", "1.9.4", leaflet_files, "https://github.com/Leaflet/Leaflet/tree/v1.9.4", {"license": {"id": "BSD-2-Clause"}})
    three_ref = purl("npm", "three", "0.184.0")
    if three_ref not in inventory.components:
        raise ValueError("Browser Three.js version is not covered by package-lock.json.")
    inventory.add({**inventory.components[three_ref], "externalReferences": [{"type": "distribution", "url": "https://cdn.jsdelivr.net/npm/three@0.184.0/build/three.module.js"}, {"type": "distribution", "url": "https://cdn.jsdelivr.net/npm/three@0.184.0/examples/jsm/"}], "properties": properties({"cadevil:cdn:integrity": "not fetched; npm archive integrity does not attest CDN response bytes"})})
    awesome_ref = asset_component(inventory, "font-awesome", "4.7.0", [], "https://github.com/FortAwesome/Font-Awesome/tree/v4.7.0", {"expression": "MIT AND OFL-1.1"}, usage="browser CDN CSS and webfonts; documentation excluded")
    inventory.add({**inventory.components[awesome_ref], "externalReferences": [{"type": "distribution", "url": "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/4.7.0/css/font-awesome.min.css"}, {"type": "license", "url": "https://fontawesome.com/v4/license/"}], "properties": properties({"cadevil:cdn:integrity": "not fetched; response bytes unverified"})})
    # Hash only the import/URL declarations, not the full template edited by
    # unrelated UI changes. This is public dependency evidence, not a source BOM.
    inventory.evidence["browser-runtime-declarations"] = sha256(serialized(declarations).encode()).hexdigest()


def cargo_inventory(inventory: Inventory) -> None:
    for folder, wasm in [("example_plugin", "example_plugin"), ("rust_example_plugin", "rust_example_plugin")]:
        directory = ROOT / "plugins" / folder
        manifest = tomllib.loads((directory / "Cargo.toml").read_text())
        lock = tomllib.loads((directory / "Cargo.lock").read_text())
        package = manifest["package"]
        refs = {(entry["name"], entry["version"]): purl("cargo", entry["name"], entry["version"]) for entry in lock["package"]}
        for entry in lock["package"]:
            ref = refs[(entry["name"], entry["version"])]
            component = {"type": "library", "bom-ref": ref, "purl": ref, "name": entry["name"], "version": entry["version"], "scope": "required", "properties": properties({"cadevil:evidence:source": (directory / "Cargo.lock").relative_to(ROOT).as_posix()})}
            if entry.get("checksum"):
                component["hashes"] = [{"alg": "SHA-256", "content": entry["checksum"]}]
            if not entry.get("source"):
                component["licenses"] = [{"license": {"id": "MIT"}}]
                component["properties"] += properties({"cadevil:license:evidence": "first-party source under repository LICENSE", "cadevil:cargo:source": "first-party local crate, not a crates.io publication"})
            else:
                component["properties"] += properties({"cadevil:cargo:source": entry["source"], "cadevil:license:evidence": "not declared in Cargo.lock; review required"})
            dependencies = []
            for dependency in entry.get("dependencies", []):
                parts = dependency.split()
                matching = [target for key, target in refs.items() if key[0] == parts[0] and (len(parts) == 1 or key[1] == parts[1])]
                if len(matching) != 1:
                    raise ValueError(f"Ambiguous Cargo dependency: {dependency}")
                dependencies.append(matching[0])
            inventory.add(component, dependencies)
        root_ref = refs[(package["name"], package["version"])]
        inventory.dependencies[inventory.component["bom-ref"]].add(root_ref)
        for path in [directory / "Cargo.lock", directory / "Cargo.toml"]:
            inventory.evidence_file(path)
        filename = (f"plugins/{wasm}/static/wasm/{wasm}.wasm"
                    if wasm == "example_plugin" else f"resources/static/wasm/{wasm}.wasm")
        asset_ref = f"urn:cadevil:asset:{filename}"
        inventory.add({"type": "file", "bom-ref": asset_ref, "name": filename, "hashes": [{"alg": "SHA-256", "content": digest(ROOT / filename)}], "scope": "required", "properties": properties({"cadevil:evidence:source": "tracked compiled WASM bytes; source/build equivalence unverified"})})
        inventory.dependencies[root_ref].add(asset_ref)
        inventory.evidence_file(ROOT / filename)


def development_tools(inventory: Inventory, licenses: dict) -> None:
    snapshot = json.loads((INPUTS / "python-tools.json").read_text())
    for name, records in snapshot["environments"].items():
        container_ref = purl("generic", f"cadevil-{name}-environment", "snapshot")
        inventory.add({"type": "application", "bom-ref": container_ref, "name": f"cadevil-{name}-environment", "version": "snapshot", "scope": "optional", "properties": properties({"cadevil:inventory:selection": "captured installed macOS arm64 CPython 3.14 tool environment; not a universal lock", "cadevil:evidence:source": "sbom/inputs/python-tools.json"})})
        inventory.dependencies[inventory.component["bom-ref"]].add(container_ref)
        names = {record["name"]: purl("pypi", record["name"], record["version"]) for record in records}
        for record in records:
            ref = names[record["name"]]
            component = {"type": "library", "bom-ref": ref, "purl": ref, "name": record["name"], "version": record["version"], "scope": "optional", "properties": properties({"cadevil:evidence:source": "sbom/inputs/python-tools.json", "cadevil:tool:environment": name, "cadevil:tool:conditional-dependencies": json.dumps(record["requiresDist"], sort_keys=True)})}
            enrich_license(component, licenses)
            dependencies = [names[canonicalize_name(Requirement(requirement).name)] for requirement in record["requiresDist"] if canonicalize_name(Requirement(requirement).name) in names]
            inventory.add(component, dependencies)
            inventory.dependencies[container_ref].add(ref)
        if name in {"code-audit", "dependency-audit", "docker", "sbom-generator"}:
            lock_path = SBOM / "tools-requirements.lock" if name == "sbom-generator" else INPUTS / f"{name}.requirements.lock"
            locked = {}
            for line in lock_path.read_text().splitlines():
                if line and not line[0].isspace() and not line.startswith("#"):
                    requirement = Requirement(line.removesuffix(" \\"))
                    if len(requirement.specifier) != 1 or next(iter(requirement.specifier)).operator != "==":
                        raise ValueError(f"Tool requirement is not exactly pinned: {line}")
                    locked[canonicalize_name(requirement.name)] = next(iter(requirement.specifier)).version
            captured = {record["name"]: record["version"] for record in records}
            if locked != captured:
                raise ValueError(f"Tool snapshot does not match {lock_path.name}: {name}")
    for name in ["context7", "ui-ux-suite"]:
        npm_inventory(inventory, INPUTS / f"{name}.package-lock.json", True, f"cadevil-{name}-environment")
    provenance = json.loads((INPUTS / "code-audit-provenance.json").read_text())
    rule_ref = purl("generic", "semgrep-rules", provenance["semgrep_rules_commit"])
    inventory.add({"type": "data", "bom-ref": rule_ref, "purl": rule_ref, "name": "semgrep-rules", "version": provenance["semgrep_rules_commit"], "scope": "optional", "licenses": [{"license": {"name": "Semgrep Rules License"}}], "externalReferences": [{"type": "distribution", "url": provenance["semgrep_rules_archive_url"], "hashes": [{"alg": "SHA-256", "content": provenance["semgrep_rules_archive_sha256"]}]}, {"type": "license", "url": f"https://github.com/semgrep/semgrep-rules/blob/{provenance['semgrep_rules_commit']}/LICENSE"}]})
    inventory.dependencies[inventory.component["bom-ref"]].add(rule_ref)
    for path in sorted(INPUTS.iterdir()):
        inventory.evidence_file(path)
    inventory.evidence_file(SBOM / "tools-requirements.lock")


def generate(uv: str, validate: bool = True) -> dict[str, str]:
    check_tool_versions(uv)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    licenses = json.loads((INPUTS / "python-license-evidence.json").read_text())
    runtime = Inventory(project["name"], project["version"], "Cadevil application dependency and declared browser/WASM inventory")
    python_export(runtime, uv, licenses, False)
    npm_inventory(runtime, ROOT / "package-lock.json", False)
    browser_pki_inventory(runtime, development=False)
    browser_inventory(runtime)
    cargo_inventory(runtime)
    runtime.evidence_file(INPUTS / "python-license-evidence.json")
    development = Inventory(f"{project['name']}-development-tools", project["version"], "Application development groups and separately captured local MCP, audit and SBOM tool environments")
    python_export(development, uv, licenses, True)
    development_tools(development, licenses)
    npm_inventory(development, ROOT / "package-lock.json", True, "cadevil-browser-test-environment")
    browser_pki_inventory(development, development=True)
    return {"cadevil.cdx.json": serialized(runtime.output(validate)), "cadevil-development.cdx.json": serialized(development.output(validate))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv", default="uv", help="Pinned uv executable (0.12.22)")
    parser.add_argument("--check", action="store_true", help="Validate coverage and deterministic regeneration without writing outputs")
    parser.add_argument("--capture-tools", type=Path, help="Refresh public metadata/lock snapshots from an explicitly selected MCP tool root")
    parser.add_argument("--application-env", type=Path, default=ROOT / ".venv")
    arguments = parser.parse_args()
    if arguments.capture_tools:
        check_tool_versions(arguments.uv)
        capture_inputs(arguments.capture_tools, arguments.application_env)
    outputs = generate(arguments.uv)
    # Equality with the already schema-validated bytes validates the second
    # independent regeneration without repeating expensive URL-format checks.
    if arguments.check and outputs != generate(arguments.uv, validate=False):
        raise ValueError("SBOM regeneration is not deterministic")
    for name, output in outputs.items():
        path = SBOM / name
        if arguments.check:
            if not path.exists() or path.read_text() != output:
                raise ValueError(f"Stale SBOM: {path.name}; run make sbom.")
        else:
            path.write_text(output)
        value = json.loads(output)
        print(f"{'Verified' if arguments.check else 'Wrote'} sbom/{name}: {len(value['components'])} components; schema and dependency graph valid")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, subprocess.CalledProcessError) as error:
        print(f"SBOM error: {error}", file=sys.stderr)
        sys.exit(1)
