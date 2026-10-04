"""Read bounded, integrity-checked plugin inventories without executing a plugin.

Bundled projections retain the audited inventory's scope. Uploaded SBOMs are
publisher declarations; the package signature authenticates bytes, not accuracy.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404

from shared.page_views import render_page
from .models import PluginRecord, UserPluginSelection
from .workflows import is_workflow_plugin, selectable_plugin

ROOT = Path(__file__).resolve().parents[1]
SBOM_FILENAME = "sbom.cdx.json"
MAX_SBOM_BYTES = 256 * 1024
MAX_AUDIT_BYTES = 2 * 1024 * 1024
# TAR uploads are normalized to an uncompressed ZIP. The original upload is
# capped at 2 MiB, but its stored container may contain 8 MiB of allowed files.
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024 + 128 * 1024
MAX_COMPONENTS = 1000
MAX_DEPENDENCIES = 1000
MAX_EDGES = 5000
MAX_TEXT = 2048
MAX_DEPTH = 12

RUST_PLUGINS = {
    "cadevil.example.editor": ("example_plugin", "example-plugin-builder"),
    "cadevil.rust-example.editor": ("rust_example_plugin", "rust_example_plugin"),
}
DEVELOPMENT_ENVIRONMENTS = {
    "cadevil.mcp.context7": "cadevil-context7-environment",
    "cadevil.mcp.git": "cadevil-git-mcp-environment",
    "cadevil.mcp.ui_ux": "cadevil-ui-ux-suite-environment",
    "cadevil.mcp.code_audit": "cadevil-code-audit-environment",
}


class InventoryUnavailable(ValueError):
    """Inventory evidence is absent, stale, malformed, or no longer trusted."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON property")
        result[key] = value
    return result


def _text(value, *, required=False):
    if not isinstance(value, str) or len(value) > MAX_TEXT or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid component text")
    if required and not value.strip():
        raise ValueError("Missing component text")
    return value


def parse_inventory(content, *, max_bytes=MAX_SBOM_BYTES):
    """Validate the supported CycloneDX 1.6 subset and resolve its graph locally.

    No URLs or external schemas are fetched. Limits apply before JSON parsing,
    then to nested components, text and dependency edges. This is a safe reader,
    not certification of all CycloneDX fields or the publisher's claims.
    """
    if not isinstance(content, bytes) or len(content) > max_bytes:
        raise ValidationError("The plugin SBOM must be at most 256 KiB.")
    try:
        document = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Invalid JSON number")))
        if not isinstance(document, dict) or document.get("bomFormat") != "CycloneDX" or document.get("specVersion") != "1.6":
            raise ValueError("Unsupported inventory format")
        if type(document.get("version")) is not int or document["version"] < 1:
            raise ValueError("Invalid document version")
        metadata = document.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("Invalid metadata")
        references = set()
        flat = []

        def component(value, depth=0, root=False):
            if not isinstance(value, dict) or depth > MAX_DEPTH or len(flat) >= MAX_COMPONENTS:
                raise ValueError("Invalid component hierarchy")
            name = _text(value.get("name"), required=True)
            kind = _text(value.get("type"), required=True)
            if kind not in {"application", "framework", "library", "container", "platform", "operating-system", "device", "device-driver", "firmware", "file", "machine-learning-model", "data", "cryptographic-asset"}:
                raise ValueError("Invalid component type")
            version = _text(value.get("version", ""))
            ref = value.get("bom-ref")
            if ref is not None:
                ref = _text(ref, required=True)
                if ref in references:
                    raise ValueError("Duplicate component reference")
                references.add(ref)
            purl = _text(value.get("purl", ""))
            licenses = value.get("licenses", [])
            if not isinstance(licenses, list) or len(licenses) > 20:
                raise ValueError("Invalid component licenses")
            labels = []
            for entry in licenses:
                if not isinstance(entry, dict):
                    raise ValueError("Invalid license entry")
                if "expression" in entry:
                    labels.append(_text(entry["expression"], required=True))
                else:
                    license_value = entry.get("license")
                    if not isinstance(license_value, dict):
                        raise ValueError("Invalid license")
                    labels.append(_text(license_value.get("id", license_value.get("name")), required=True))
            hashes = value.get("hashes", [])
            if not isinstance(hashes, list) or len(hashes) > 20:
                raise ValueError("Invalid component hashes")
            hash_labels = []
            for digest in hashes:
                if not isinstance(digest, dict):
                    raise ValueError("Invalid hash")
                algorithm = _text(digest.get("alg"), required=True)
                encoded = _text(digest.get("content"), required=True)
                hash_labels.append(f"{algorithm}: {encoded}")
            children = value.get("components", [])
            if not isinstance(children, list):
                raise ValueError("Invalid nested components")
            if not root:
                flat.append({"name": name, "kind": kind, "version": version, "reference": ref or "",
                             "purl": purl, "licenses": "; ".join(labels) or "Not declared",
                             "hashes": hash_labels})
            for child in children:
                component(child, depth + 1)

        if "component" in metadata:
            component(metadata["component"], root=True)
        components = document.get("components", [])
        if not isinstance(components, list):
            raise ValueError("Invalid components")
        for value in components:
            component(value)
        dependencies = document.get("dependencies", [])
        if not isinstance(dependencies, list) or len(dependencies) > MAX_DEPENDENCIES:
            raise ValueError("Invalid dependencies")
        seen = set()
        edge_count = 0
        for dependency in dependencies:
            if not isinstance(dependency, dict):
                raise ValueError("Invalid dependency entry")
            ref = _text(dependency.get("ref"), required=True)
            targets = dependency.get("dependsOn", [])
            if ref not in references or ref in seen or not isinstance(targets, list):
                raise ValueError("Invalid dependency reference")
            seen.add(ref)
            edge_count += len(targets)
            if edge_count > MAX_EDGES:
                raise ValueError("Too many dependency edges")
            for target in targets:
                if _text(target, required=True) not in references:
                    raise ValueError("Unresolved dependency reference")
        return document, sorted(flat, key=lambda item: (item["name"].casefold(), item["version"], item["reference"]))
    except (ValueError, TypeError, UnicodeDecodeError, RecursionError, OverflowError) as error:
        raise ValidationError("Include valid CycloneDX 1.6 JSON in sbom.cdx.json, with unique component references and no unresolved dependencies.") from error


def _serialize(document):
    return (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


def _read_inventory(name):
    # Only caller-selected, fixed audited filenames are accepted here.
    try:
        with (ROOT / "sbom" / name).open("rb") as stream:
            content = stream.read(MAX_AUDIT_BYTES + 1)
        document, _ = parse_inventory(content, max_bytes=MAX_AUDIT_BYTES)
        return document, hashlib.sha256(content).hexdigest()
    except (OSError, ValidationError) as error:
        raise InventoryUnavailable("The audited inventory is unavailable. Regenerate the application SBOM.") from error


def _plugin_document(record, components, dependencies, scope, source_digest="", roots=None):
    root = f"urn:cadevil:plugin:{record.plugin_id}"
    properties = [{"name": "cadevil:inventory:scope", "value": scope}]
    if source_digest:
        properties.append({"name": "cadevil:evidence:source-sha256", "value": source_digest})
    return {"$schema": "http://cyclonedx.org/schema/bom-1.6.schema.json", "bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1,
            "metadata": {"component": {"type": "application", "bom-ref": root, "name": record.name or record.plugin_id,
                                         "version": record.version or "unknown", "properties": properties}},
            "components": components,
            "dependencies": [{"ref": root, "dependsOn": sorted(roots if roots is not None else [component["bom-ref"] for component in components])}, *dependencies],
            "compositions": [{"aggregate": "incomplete", "assemblies": [root]}]}


def _project(record, document, roots, source_digest, scope):
    components = {component["bom-ref"]: component for component in document.get("components", [])}
    graph = {dependency["ref"]: dependency.get("dependsOn", []) for dependency in document.get("dependencies", [])}
    selected = set()
    pending = list(roots)
    while pending:
        ref = pending.pop()
        if ref in selected:
            continue
        if ref not in components:
            raise InventoryUnavailable("The audited inventory does not contain this plugin's declared components.")
        selected.add(ref)
        pending.extend(graph.get(ref, []))
    if not selected:
        raise InventoryUnavailable("No audited components are available for this plugin.")
    dependencies = [{"ref": ref, "dependsOn": sorted(set(graph.get(ref, [])) & selected)} for ref in sorted(selected)]
    return _plugin_document(record, [components[ref] for ref in sorted(selected)], dependencies, scope, source_digest, roots=roots)


def _builtin_inventory(record):
    if record.plugin_id in DEVELOPMENT_ENVIRONMENTS:
        document, source_digest = _read_inventory("cadevil-development.cdx.json")
        name = DEVELOPMENT_ENVIRONMENTS[record.plugin_id]
        roots = [component["bom-ref"] for component in document["components"] if component["name"] == name]
        scope = "Captured development tool environment and dependency closure; not an installed deployment or native-library inventory."
        return _project(record, document, roots, source_digest, scope), scope, "Audited development inventory"
    document, source_digest = _read_inventory("cadevil.cdx.json")
    if record.plugin_id in RUST_PLUGINS:
        folder, package_name = RUST_PLUGINS[record.plugin_id]
        roots = [component["bom-ref"] for component in document["components"] if component["name"] == package_name]
        filename = (f"plugins/{folder}/static/wasm/{folder}.wasm"
                    if folder == "example_plugin" else f"resources/static/wasm/{folder}.wasm")
        files = [component for component in document["components"] if component["name"] == filename]
        try:
            expected = next(digest["content"] for digest in files[0]["hashes"] if digest["alg"] == "SHA-256")
            actual = hashlib.sha256((ROOT / filename).read_bytes()).hexdigest()
        except (OSError, IndexError, KeyError, StopIteration) as error:
            raise InventoryUnavailable("The compiled plugin's inventory evidence is unavailable.") from error
        if expected != actual:
            raise InventoryUnavailable("The compiled plugin changed after its SBOM was generated. Regenerate the application SBOM.")
        scope = "Bundled Cargo dependency closure and tracked WASM hash. Rust standard library, compiler and source-to-binary equivalence are not attested."
        return _project(record, document, roots, source_digest, scope), scope, "Audited Cargo and WASM inventory"
    if record.plugin_id == "cadevil.bim.model_manager":
        scope = "Shared application runtime used by the BIM workspace. Includes host components shared with other pages; not a plugin-exclusive dependency audit. OS packages, native libraries and CDN response bytes are outside this inventory."
        host_ref = document["metadata"]["component"]["bom-ref"]
        roots = next(dependency["dependsOn"] for dependency in document["dependencies"] if dependency["ref"] == host_ref)
        return _project(record, document, roots, source_digest, scope), scope, "Shared audited application runtime"
    if record.plugin_id == "cadevil.mcp.native":
        scope = "Shared django-bolt host runtime dependency closure for the debug MCP interface; not a separately packaged MCP process."
        roots = [component["bom-ref"] for component in document["components"] if component["name"] == "django-bolt"]
        return _project(record, document, roots, source_digest, scope), scope, "Audited shared host dependency closure"
    raise InventoryUnavailable("This installed Python plugin has no published SBOM. Ask its publisher for an inventory before assessing its dependencies.")


def _uploaded_inventory(record):
    from .signatures import verify_package_signature
    if record.artifact_type != PluginRecord.ArtifactType.ZIP or not record.artifact:
        raise InventoryUnavailable("This upload has no signed archive inventory.")
    manifest = record.package_manifest
    try:
        key = verify_package_signature(manifest)
        if key.pk != record.signing_key_id:
            raise ValidationError("Signing key mismatch")
        files = manifest["files"]
        if not isinstance(files, dict) or not files or len(files) > 32:
            raise ValidationError("Invalid signed file inventory")
        with record.artifact.open("rb") as stream:
            archive_bytes = stream.read(MAX_ARCHIVE_BYTES + 1)
        if len(archive_bytes) > MAX_ARCHIVE_BYTES or hashlib.sha256(archive_bytes).hexdigest() != record.content_hash:
            raise ValidationError("Changed archive bytes")
        if SBOM_FILENAME in files:
            with ZipFile(io.BytesIO(archive_bytes)) as archive:
                members = [member for member in archive.infolist() if member.filename == SBOM_FILENAME]
                if len(members) != 1 or members[0].file_size > MAX_SBOM_BYTES:
                    raise ValidationError("Invalid SBOM member")
                content = archive.read(members[0])
            if hashlib.sha256(content).hexdigest() != files[SBOM_FILENAME]:
                raise ValidationError("Changed SBOM bytes")
            document, _ = parse_inventory(content)
            scope = "Publisher-declared CycloneDX components. The SBOM's SHA-256 is bound to the package's registered Ed25519 signature. Accuracy, completeness, licenses and vulnerability status have not been independently verified."
            return document, scope, "Signed publisher SBOM", content
        from .packages import safe_path
        components = []
        for filename, digest in sorted(files.items()):
            safe_path(filename)
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValidationError("Invalid signed file hash")
            components.append({"type": "file", "bom-ref": f"urn:cadevil:package-file:{filename}", "name": filename,
                               "hashes": [{"alg": "SHA-256", "content": digest}]})
        scope = "Signed package file inventory only. The publisher supplied no sbom.cdx.json; third-party dependencies, embedded libraries and licenses are not inventoried."
        return _plugin_document(record, components, [], scope), scope, "Signed files; dependency SBOM not supplied", None
    except (ValidationError, OSError, ValueError, KeyError, BadZipFile, RuntimeError, NotImplementedError, TypeError) as error:
        raise InventoryUnavailable("The package inventory could not be verified. Its signing key may be unavailable or its archive may have changed.") from error


def plugin_inventory(record):
    if record.source == PluginRecord.Source.UPLOAD:
        document, scope, source, signed_content = _uploaded_inventory(record)
    else:
        document, scope, source = _builtin_inventory(record)
        signed_content = None
    # Preserve uploaded SBOM bytes exactly: the displayed/downloaded SHA-256 is
    # the hash authenticated by the package signature, not a JSON reformatting.
    content = signed_content if signed_content is not None else _serialize(document)
    try:
        _, components = parse_inventory(content, max_bytes=MAX_AUDIT_BYTES)
    except ValidationError as error:
        raise InventoryUnavailable("The plugin inventory exceeds the supported format or size limits.") from error
    return {"content": content, "components": components, "scope": scope, "source": source,
            "component_count": len(components), "sha256": hashlib.sha256(content).hexdigest()}


def _visible_record(request, plugin_id):
    record = get_object_or_404(PluginRecord.objects.select_related("signing_key__owner"), plugin_id=plugin_id)
    if not record.environment_compatible:
        raise Http404("This plugin is unavailable in this environment.")
    selected = UserPluginSelection.objects.filter(user=request.user, plugin=record).exists()
    if not request.user.is_staff and not selectable_plugin(record) and not (selected and is_workflow_plugin(record)):
        raise Http404("This plugin is unavailable in your catalog.")
    return record


@login_required(login_url="/mycelium/login")
def plugin_sbom(request, plugin_id):
    record = _visible_record(request, plugin_id)
    try:
        inventory = plugin_inventory(record)
        notice = ""
    except InventoryUnavailable as error:
        inventory = None
        notice = str(error)
    response = render_page(request, "plugin_manager/sbom.html", {"record": record, "inventory": inventory, "notice": notice})
    response["Cache-Control"] = "private, no-store"
    return response


@login_required(login_url="/mycelium/login")
def plugin_sbom_download(request, plugin_id):
    record = _visible_record(request, plugin_id)
    try:
        inventory = plugin_inventory(record)
    except InventoryUnavailable as error:
        raise Http404("This plugin's SBOM is unavailable.") from error
    response = HttpResponse(inventory["content"], content_type="application/vnd.cyclonedx+json")
    # A fixed filename avoids putting third-party manifest data into headers.
    response["Content-Disposition"] = 'attachment; filename="cadevil-plugin.cdx.json"'
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
