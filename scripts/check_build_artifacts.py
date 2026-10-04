"""Verify exact application distributions and extract only reviewed wheel files."""
import argparse
import base64
import csv
from email.parser import BytesParser
from hashlib import sha256
import io
import json
import re
from pathlib import Path, PurePosixPath
import stat
import tarfile
import tomllib
import zipfile

MANIFEST = "docker/image-files.json"
GENERATED = {
    "plugins/example_plugin/static/wasm/example_plugin.wasm",
    "resources/static/wasm/rust_example_plugin.wasm", "sbom/cadevil.cdx.json",
}
WASM = GENERATED - {"sbom/cadevil.cdx.json"}
HOOK_INPUTS = ("hatch_build.py", "rust-toolchain.toml", "scripts/check_build_artifacts.py")
PRIVATE_PARTS = {".git", ".env", ".venv", "node_modules", "__pycache__", "data", "media", "reference", "target", "tests", ".ssh", ".aws", ".codex", ".agents", "backups", "development_mcp"}


def canonical(value):
    if not isinstance(value, str) or not value or any(character in value for character in "\\*?[]") or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Distribution paths must be exact, control-free relative paths")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part in {".", ".."} for part in path.parts):
        raise ValueError("Noncanonical distribution path: " + value)
    return path


def regular_file(root, value):
    current = Path(root)
    for part in canonical(value).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Symlink in distribution input: " + value)
    if not current.is_file():
        raise ValueError("Missing distribution input: " + value)
    return current


def reviewed_manifest(root):
    document = json.loads(regular_file(root, MANIFEST).read_text())
    if set(document) != {"version", "runtime", "build_only"} or type(document["version"]) is not int or document["version"] != 1:
        raise ValueError("Unsupported distribution manifest")
    seen = set()
    for group in ("runtime", "build_only"):
        if not isinstance(document[group], list):
            raise ValueError("Distribution input groups must be lists")
        for value in document[group]:
            path = canonical(value)
            if value in seen:
                raise ValueError("Duplicate distribution input: " + value)
            seen.add(value)
            if any(part in PRIVATE_PARTS or (part.startswith(".") and part != ".well-known") for part in path.parts) and value != ".dockerignore":
                raise ValueError("Private/development distribution input: " + value)
            if path.name.startswith("test") or ".test." in path.name or path.suffix.lower() in {".pem", ".key", ".sqlite", ".sqlite3", ".db", ".log", ".zip", ".tar", ".gz"}:
                raise ValueError("State/test distribution input: " + value)
            if group == "runtime" and ("src" in path.parts or path.suffix == ".rs" or path.name in {"Cargo.toml", "Cargo.lock", "hatch_build.py", "rust-toolchain.toml", "debugserver.py", "debug_processes.py", "mcp_bridge.py", "code_audit_bridge.py", "ux_mcp_bridge.py"}):
                raise ValueError("Build/debug code in runtime distribution: " + value)
    return document


def derived_sbom(original, hashes, toolchain):
    """Preserve the inventory/graph; change only the two rebuilt asset records."""
    document = json.loads(json.dumps(original))
    described = set()
    for component in document.get("components", []):
        name = component.get("name")
        if name in hashes:
            if name in described:
                raise ValueError("Duplicate WASM asset in source SBOM")
            component["hashes"] = [{"alg": "SHA-256", "content": hashes[name]}]
            component["properties"] = [value for value in component.get("properties", []) if value["name"] != "cadevil:evidence:source"] + [
                {"name": "cadevil:evidence:source", "value": "Built from reviewed Cargo sources by the Hatch wheel hook"},
                {"name": "cadevil:build:rust-toolchain", "value": toolchain},
                {"name": "cadevil:build:target", "value": "wasm32-unknown-unknown"},
            ]
            described.add(name)
    if described != WASM or set(hashes) != WASM:
        raise ValueError("Source SBOM lacks the two reviewed WebAssembly assets")
    for property in document.get("metadata", {}).get("properties", []):
        for name, digest in hashes.items():
            if property["name"] == "cadevil:input:sha256:" + name:
                property["value"] = digest
    return document


def validate_source_sbom(document, root, project):
    component = document.get("metadata", {}).get("component", {})
    if (component.get("name"), component.get("version"), component.get("type")) != (project["name"], project["version"], "application"):
        raise ValueError("Source SBOM application identity differs from pyproject.toml")
    inputs = [value for value in document.get("metadata", {}).get("properties", []) if value["name"].startswith("cadevil:input:sha256:")]
    by_name = {value["name"].removeprefix("cadevil:input:sha256:"): value["value"] for value in inputs}
    required = {"pyproject.toml", "uv.lock", "LICENSE"} | WASM | {f"plugins/{folder}/{name}" for folder in ("example_plugin", "rust_example_plugin") for name in ("Cargo.toml", "Cargo.lock")}
    if len(by_name) != len(inputs) or not required <= set(by_name):
        raise ValueError("Source SBOM input evidence is missing or duplicated")
    for name, digest in by_name.items():
        path = Path(root) / canonical(name)
        if name in required or path.exists():
            if digest != sha256(regular_file(root, name).read_bytes()).hexdigest():
                raise ValueError("Source SBOM input evidence is stale: " + name)


def dependency_key(value):
    # Hatch normalizes the ordering of comma-separated version specifiers.
    match = re.fullmatch(r"([A-Za-z0-9_.-]+(?:\[[^]]+\])?)(.*)", value.replace(" ", ""))
    if not match:
        raise ValueError("Invalid project dependency")
    return match[1].lower().replace("_", "-"), tuple(sorted(match[2].split(",")))


def wheel_payload(wheel, source):
    manifest = reviewed_manifest(source)
    expected = set(manifest["runtime"])
    with zipfile.ZipFile(wheel) as archive:
        members = {}
        metadata_root = None
        for info in archive.infolist():
            path = canonical(info.filename)
            mode = info.external_attr >> 16
            if info.is_dir() or stat.S_IFMT(mode) not in {0, stat.S_IFREG} or info.filename in members:
                raise ValueError("Unsafe or duplicate wheel member")
            if path.parts[0].endswith(".dist-info"):
                if metadata_root is not None and metadata_root != path.parts[0]:
                    raise ValueError("Multiple wheel metadata roots")
                metadata_root = path.parts[0]
            elif info.filename not in expected:
                raise ValueError("Unreviewed wheel payload: " + info.filename)
            members[info.filename] = info
        if metadata_root is None or not expected <= set(members):
            raise ValueError("Wheel lacks reviewed runtime payload or metadata")
        allowed_metadata = {"METADATA", "WHEEL", "RECORD", "entry_points.txt", "licenses/LICENSE", "extra_metadata/cadevil-build.json"}
        for value in members:
            if value.startswith(metadata_root + "/") and value.removeprefix(metadata_root + "/") not in allowed_metadata:
                raise ValueError("Unexpected wheel metadata file: " + value)
        record_path = metadata_root + "/RECORD"
        rows = list(csv.reader(io.StringIO(archive.read(record_path).decode())))
        if len(rows) != len(members) or {row[0] for row in rows} != set(members):
            raise ValueError("Wheel RECORD coverage differs from the archive")
        for name, digest, size in rows:
            content = archive.read(name)
            if name == record_path:
                if digest or size:
                    raise ValueError("Wheel RECORD must not hash itself")
            elif digest != "sha256=" + base64.urlsafe_b64encode(sha256(content).digest()).rstrip(b"=").decode() or size != str(len(content)):
                raise ValueError("Wheel RECORD content mismatch: " + name)
        project = tomllib.loads(regular_file(source, "pyproject.toml").read_text())["project"]
        if metadata_root != project["name"].replace("-", "_") + "-" + project["version"] + ".dist-info":
            raise ValueError("Wheel metadata root differs from the project identity")
        metadata = BytesParser().parsebytes(archive.read(metadata_root + "/METADATA"))
        wheel_metadata = BytesParser().parsebytes(archive.read(metadata_root + "/WHEEL"))
        allowed_headers = {"Metadata-Version", "Name", "Version", "Summary", "License-File", "Requires-Python", "Requires-Dist", "Description-Content-Type"}
        if set(metadata.keys()) != allowed_headers or any(len(metadata.get_all(name, [])) != 1 for name in allowed_headers - {"Requires-Dist"}):
            raise ValueError("Wheel metadata has unexpected or duplicate identity headers")
        if metadata["Metadata-Version"] != "2.4":
            raise ValueError("Unreviewed core metadata version")
        if (metadata["Name"], metadata["Version"], metadata["Requires-Python"]) != (project["name"], project["version"], project["requires-python"]):
            raise ValueError("Wheel project identity differs from pyproject.toml")
        if metadata["Summary"] != project["description"] or metadata["Description-Content-Type"] != "text/markdown" or metadata.get_payload(decode=True).strip() != regular_file(source, project["readme"]).read_bytes().strip():
            raise ValueError("Wheel description/readme differs from reviewed project metadata")
        if metadata["License-File"] != "LICENSE" or archive.read(metadata_root + "/licenses/LICENSE") != regular_file(source, "LICENSE").read_bytes():
            raise ValueError("Wheel license differs from reviewed source LICENSE")
        if {dependency_key(value) for value in metadata.get_all("Requires-Dist", [])} != {dependency_key(value) for value in project["dependencies"]}:
            raise ValueError("Wheel dependencies differ from pyproject.toml")
        if set(wheel_metadata.keys()) != {"Wheel-Version", "Generator", "Root-Is-Purelib", "Tag"} or any(len(wheel_metadata.get_all(name, [])) != 1 for name in wheel_metadata.keys()) or wheel_metadata["Wheel-Version"] != "1.0" or wheel_metadata["Generator"] != "hatchling 1.32.4" or wheel_metadata.get_payload(decode=True).strip():
            raise ValueError("Unexpected/duplicate wheel generator metadata")
        if wheel_metadata["Root-Is-Purelib"] != "true" or wheel_metadata.get_all("Tag") != ["py3-none-any"]:
            raise ValueError("Wheel must contain portable Python/browser resources")
        expected_entry_points = "\n".join(
            "[" + group + "]\n" + "\n".join(name + " = " + value for name, value in sorted(entries.items()))
            for group, entries in sorted(project.get("entry-points", {}).items())
        ) + "\n"
        if archive.read(metadata_root + "/entry_points.txt") != expected_entry_points.encode():
            raise ValueError("Wheel entry points differ from pyproject.toml")
        payload = {name: archive.read(name) for name in expected}
        for name, content in payload.items():
            if name not in GENERATED and content != regular_file(source, name).read_bytes():
                raise ValueError("Wheel source hash differs from reviewed input: " + name)
        for name in WASM:
            if name not in payload or not payload[name].startswith(b"\x00asm\x01\x00\x00\x00"):
                raise ValueError("Compiled WASM asset missing or invalid")
        bom = json.loads(payload["sbom/cadevil.cdx.json"])
        toolchain = tomllib.loads(regular_file(source, "rust-toolchain.toml").read_text())["toolchain"]
        if toolchain["channel"] != "1.98.1" or toolchain["targets"] != ["wasm32-unknown-unknown"]:
            raise ValueError("Unreviewed Rust toolchain/target")
        source_bom = json.loads(regular_file(source, "sbom/cadevil.cdx.json").read_text())
        validate_source_sbom(source_bom, source, project)
        expected_bom = derived_sbom(source_bom,
                                    {name: sha256(payload[name]).hexdigest() for name in WASM}, toolchain["channel"])
        if bom != expected_bom:
            raise ValueError("Derived SBOM changed unreviewed inventory/graph content")
        evidence_name = metadata_root + "/extra_metadata/cadevil-build.json"
        evidence = json.loads(archive.read(evidence_name))
        if set(evidence) != {"rust_toolchain", "rustc", "wasm_target", "cargo_source_sha256", "build_hook_sha256", "reviewed_manifest_sha256", "runtime_sha256"}:
            raise ValueError("Unexpected wheel build evidence fields")
        if evidence["runtime_sha256"] != {name: sha256(content).hexdigest() for name, content in payload.items()}:
            raise ValueError("Wheel build evidence differs from payload hashes")
        cargo_hashes = {f"plugins/{folder}/{name}": sha256(regular_file(source, f"plugins/{folder}/{name}").read_bytes()).hexdigest()
                        for folder in ("example_plugin", "rust_example_plugin") for name in ("Cargo.toml", "Cargo.lock", "src/lib.rs", "build.rs")}
        if evidence["cargo_source_sha256"] != cargo_hashes or evidence["reviewed_manifest_sha256"] != sha256(regular_file(source, MANIFEST).read_bytes()).hexdigest():
            raise ValueError("Wheel build evidence does not match reviewed source inputs")
        if evidence["build_hook_sha256"] != {name: sha256(regular_file(source, name).read_bytes()).hexdigest() for name in HOOK_INPUTS}:
            raise ValueError("Wheel build hook evidence differs from reviewed sources")
        if evidence["rust_toolchain"] != toolchain["channel"] or evidence["wasm_target"] != toolchain["targets"][0] or not re.fullmatch(r"rustc 1\.98\.1 \([a-f0-9]{7,40} \d{4}-\d{2}-\d{2}\)", evidence["rustc"]):
            raise ValueError("Wheel build evidence differs from the pinned Rust toolchain")
    return payload, evidence


def audit(wheel, source, sdist=None, output=None, extract=None, sbom_schema=None):
    payload, evidence = wheel_payload(wheel, source)
    report = {"status": "passed", "wheel_sha256": sha256(Path(wheel).read_bytes()).hexdigest(),
              "runtime_file_count": len(payload), "runtime_sha256": {name: sha256(content).hexdigest() for name, content in sorted(payload.items())}, "build_evidence": evidence}
    if sdist is not None:
        expected = set(sum((reviewed_manifest(source)[group] for group in ("runtime", "build_only")), [])) | {"PKG-INFO"}
        project = tomllib.loads(regular_file(source, "pyproject.toml").read_text())["project"]
        expected_root = project["name"].replace("-", "_") + "-" + project["version"]
        with tarfile.open(sdist, "r:*") as archive:
            observed = {}
            root = None
            for member in archive.getmembers():
                path = canonical(member.name)
                if root is None:
                    root = path.parts[0]
                    if root != expected_root:
                        raise ValueError("Source archive root differs from project identity")
                if path.parts[0] != root or len(path.parts) < 2 or not member.isfile():
                    raise ValueError("Unsafe source archive member")
                relative = path.relative_to(root).as_posix()
                if relative not in expected or relative in observed:
                    raise ValueError("Unreviewed/duplicate source archive input: " + relative)
                observed[relative] = archive.extractfile(member).read()
            if set(observed) != expected:
                raise ValueError("Source archive differs from reviewed inputs")
            for name, content in observed.items():
                if name != "PKG-INFO" and content != regular_file(source, name).read_bytes():
                    raise ValueError("Source archive hash differs: " + name)
            with zipfile.ZipFile(wheel) as built:
                metadata = next(name for name in built.namelist() if name.endswith(".dist-info/METADATA"))
                if observed["PKG-INFO"] != built.read(metadata):
                    raise ValueError("Source archive metadata differs from verified wheel metadata")
        report["sdist_sha256"] = sha256(Path(sdist).read_bytes()).hexdigest()
        report["sdist_reviewed_file_count"] = len(expected) - 1
    if sbom_schema:
        from jsonschema import Draft7Validator
        schema = json.loads(Path(sbom_schema).read_text())
        Draft7Validator.check_schema(schema)
        Draft7Validator(schema).validate(json.loads(payload["sbom/cadevil.cdx.json"]))
        report["derived_sbom_schema_validation"] = "passed"
    if extract:
        destination = Path(extract)
        if destination.exists():
            raise ValueError("Runtime extraction destination must be new")
        destination.mkdir(parents=True)
        for name, content in payload.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            target.chmod(0o644)
    if output:
        Path(output).write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--sdist", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--extract-runtime", type=Path)
    parser.add_argument("--sbom-schema", type=Path)
    args = parser.parse_args()
    result = audit(args.wheel, args.source, args.sdist, args.output, args.extract_runtime, args.sbom_schema)
    print(json.dumps({key: value for key, value in result.items() if key in {"status", "runtime_file_count", "sdist_reviewed_file_count", "wheel_sha256", "sdist_sha256", "derived_sbom_schema_validation"}}))
