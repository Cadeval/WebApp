"""Plugin inventory evidence, archive integrity and catalog access regression tests."""
import copy
import base64
import io
import tarfile
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings

from .models import PluginRecord, UserPluginSelection
from .sbom import MAX_COMPONENTS, MAX_SBOM_BYTES, InventoryUnavailable, parse_inventory, plugin_inventory
from .signatures import canonical_payload
from . import test_store as store_tests
from . import tests as native_tests
from .test_store import package
from .packages import validate_package


DOCUMENT = {"bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1,
            "metadata": {"component": {"type": "application", "name": "ZIP Calculator", "version": "1.2.3", "bom-ref": "app"}},
            "components": [{"type": "library", "name": "declared-helper", "version": "1.0.0", "bom-ref": "helper",
                            "purl": "pkg:generic/declared-helper@1.0.0", "licenses": [{"expression": "MIT"}]}],
            "dependencies": [{"ref": "app", "dependsOn": ["helper"]}, {"ref": "helper", "dependsOn": []}]}


def encoded(document=DOCUMENT):
    return json.dumps(document).encode("utf-8")


class PluginSBOMValidationTests(SimpleTestCase):
    def test_only_fixed_filename_accepts_inventory_json_in_packages(self):
        result = validate_package(package(extra={"sbom.cdx.json": encoded()}))
        self.assertEqual(result["files"]["sbom.cdx.json"], hashlib.sha256(encoded()).hexdigest())
        for name in ["other.json", "nested/sbom.cdx.json", "../sbom.cdx.json"]:
            with self.subTest(name=name), self.assertRaises(ValidationError):
                validate_package(package(extra={name: encoded()}))

    def test_invalid_json_size_duplicate_refs_and_graph_edges_are_rejected(self):
        duplicate = copy.deepcopy(DOCUMENT)
        duplicate["components"].append(duplicate["components"][0])
        dangling = copy.deepcopy(DOCUMENT)
        dangling["dependencies"][0]["dependsOn"] = ["undeclared"]
        wrong_type = {**DOCUMENT, "components": {}}
        invalid_version = {**DOCUMENT, "version": True}
        invalid_type = copy.deepcopy(DOCUMENT)
        invalid_type["components"][0]["type"] = "bogus"
        invalid_license = copy.deepcopy(DOCUMENT)
        invalid_license["components"][0]["licenses"] = [{"license": None}]
        too_many = {**DOCUMENT, "components": [{"type": "file", "name": f"file{i}", "bom-ref": f"file{i}"} for i in range(MAX_COMPONENTS + 1)]}
        nested = {"type": "library", "name": "root"}
        for _ in range(14):
            nested = {"type": "library", "name": "parent", "components": [nested]}
        for content in [b"not JSON", b'{"bomFormat":"CycloneDX","bomFormat":"CycloneDX"}', b'{"bomFormat":"CycloneDX","x":NaN}', b' ' * (MAX_SBOM_BYTES + 1),
                        encoded(duplicate), encoded(dangling), encoded(wrong_type), encoded(invalid_version), encoded(invalid_type), encoded(invalid_license), encoded(too_many), encoded({**DOCUMENT, "components": [nested]})]:
            with self.subTest(prefix=content[:60]), self.assertRaises(ValidationError):
                parse_inventory(content)

    def test_nested_components_are_counted_without_fetching_external_references(self):
        document = copy.deepcopy(DOCUMENT)
        document["components"][0]["components"] = [{"type": "file", "name": "nested.js", "bom-ref": "nested"}]
        document["components"][0]["externalReferences"] = [{"type": "distribution", "url": "http://127.0.0.1:1/private"}]
        with patch("urllib.request.urlopen", side_effect=AssertionError("Network access")):
            _, components = parse_inventory(encoded(document))
        self.assertEqual({component["name"] for component in components}, {"declared-helper", "nested.js"})

    def test_local_signing_cli_accepts_the_fixed_sbom_and_binds_its_bytes(self):
        from .sign_cli import read_package
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder:
            filename = Path(folder) / "plugin.zip"
            filename.write_bytes(package(extra={"sbom.cdx.json": encoded()}))
            files = read_package(filename)
            self.assertEqual(files["sbom.cdx.json"], encoded())
            signature_payload = json.loads(canonical_payload({name: hashlib.sha256(content).hexdigest() for name, content in files.items()}))
            self.assertEqual(signature_payload["files"]["sbom.cdx.json"], hashlib.sha256(encoded()).hexdigest())


class PluginBuiltinInventoryTests(TestCase):
    def record(self, plugin_id, **kwargs):
        return PluginRecord.objects.create(plugin_id=plugin_id, name="Inventory tool", version="2.0.0", **kwargs)

    def test_real_committed_rust_inventories_include_only_matching_wasm_and_crate(self):
        for plugin_id, wasm, crate in [("cadevil.example.editor", "example_plugin", "example-plugin-builder"),
                                      ("cadevil.rust-example.editor", "rust_example_plugin", "rust_example_plugin")]:
            with self.subTest(plugin=plugin_id):
                inventory = plugin_inventory(self.record(plugin_id))
                document = json.loads(inventory["content"])
                names = {component["name"] for component in document["components"]}
                filename = (f"apps/plugins/{wasm}/static/wasm/{wasm}.wasm"
                            if wasm == "example_plugin" else f"resources/static/wasm/{wasm}.wasm")
                self.assertEqual(names, {crate, filename})
                self.assertIn("source-to-binary equivalence", inventory["scope"])
                self.assertEqual(inventory["component_count"], 2)
                self.assertEqual(document["compositions"][0]["aggregate"], "incomplete")
                self.assertNotIn("uploaded_by", inventory["content"].decode())

    def test_rust_bytes_changed_since_audit_are_unavailable(self):
        record = self.record("cadevil.example.editor")
        with patch("apps.plugin_manager.sbom.Path.read_bytes", return_value=b"changed wasm"), self.assertRaises(InventoryUnavailable):
            plugin_inventory(record)

    def test_real_shared_bim_runtime_and_development_graph_are_readable(self):
        inventory = plugin_inventory(self.record("cadevil.bim.model_manager"))
        self.assertIn("Shared application runtime", inventory["scope"])
        self.assertGreater(inventory["component_count"], 50)
        for plugin_id in ["cadevil.mcp.context7", "cadevil.mcp.git", "cadevil.mcp.ui_ux", "cadevil.mcp.code_audit", "cadevil.mcp.native"]:
            with self.subTest(plugin=plugin_id):
                development = plugin_inventory(self.record(plugin_id, compatibility="debug"))
                self.assertGreater(development["component_count"], 1)
                self.assertNotIn("/Users/", development["content"].decode())


class PluginSBOMPageTests(TestCase):
    def setUp(self):
        native_tests.NativePluginTests.setUp(self)
        self.builtin = PluginRecord.objects.create(plugin_id="cadevil.example.editor", name="Rust IFC Editor", version="2.0.1", enabled=True)

    def url(self, record=None, *, download=False):
        return f"/plugins/{(record or self.builtin).plugin_id}/{'sbom.json' if download else 'sbom/'}"

    def test_anonymous_login_and_available_catalog_user_access(self):
        self.client.logout()
        for download in [False, True]:
            response = self.client.get(self.url(download=download))
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith("/mycelium/login"))
        self.client.force_login(self.regular)
        response = self.client.get(self.url())
        self.assertContains(response, "Rust IFC Editor SBOM")
        self.assertContains(self.client.get("/plugins/manage/"), 'hx-get="' + self.url() + '"')
        self.assertEqual(self.client.get(self.url(download=True)).status_code, 200)

    def test_full_and_htmx_fragment_keep_one_content_boundary_and_no_admin_actions(self):
        self.client.force_login(self.regular)
        full = self.client.get(self.url())
        self.assertContains(full, "<html")
        self.assertContains(full, 'id="content-container"', count=1)
        fragment = self.client.get(self.url(), HTTP_HX_REQUEST="true")
        self.assertNotContains(fragment, "<html")
        self.assertContains(fragment, 'id="content-container"', count=1)
        self.assertContains(fragment, "Download plugin SBOM")
        self.assertNotContains(fragment, "Disable globally")
        self.assertEqual(fragment["Cache-Control"], "private, no-store")
        history = self.client.get(self.url(), HTTP_HX_REQUEST="true", HTTP_HX_HISTORY_RESTORE_REQUEST="true")
        self.assertContains(history, "<html")
        self.assertContains(history, 'id="content-container"', count=1)

    def test_download_is_json_attachment_without_sensitive_query_or_header_reflection(self):
        response = self.client.get(self.url(download=True), {"file": "/etc/passwd", "plugin_id": "other"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/vnd.cyclonedx+json")
        self.assertEqual(response["Content-Disposition"], 'attachment; filename="cadevil-plugin.cdx.json"')
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertNotIn("/etc/passwd", response.content.decode())
        self.assertEqual(json.loads(response.content)["metadata"]["component"]["version"], "2.0.1")

    def test_disabled_unselected_plugins_are_private_but_saved_catalog_selection_is_preserved(self):
        self.builtin.enabled = False
        self.builtin.save()
        self.client.force_login(self.regular)
        for download in [False, True]:
            self.assertEqual(self.client.get(self.url(download=download)).status_code, 404)
        UserPluginSelection.objects.create(user=self.regular, plugin=self.builtin)
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    @override_settings(DEBUG=False)
    def test_debug_only_plugins_and_inventory_are_unavailable_in_production(self):
        record = PluginRecord.objects.create(plugin_id="cadevil.mcp.context7", name="Context7", enabled=True, compatibility="debug")
        for user in [self.staff, self.regular]:
            self.client.force_login(user)
            for download in [False, True]:
                self.assertEqual(self.client.get(self.url(record, download=download)).status_code, 404)
            catalog = self.client.get("/plugins/manage/")
            self.assertNotContains(catalog, self.url(record))

    @override_settings(DEBUG=True)
    def test_development_sbom_is_staff_only_even_with_a_saved_nonworkflow_selection(self):
        record = PluginRecord.objects.create(plugin_id="cadevil.mcp.context7", name="Context7", enabled=True, compatibility="debug")
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(self.url(record)), "Audited development inventory")
        self.assertEqual(self.client.get(self.url(record, download=True)).status_code, 200)
        UserPluginSelection.objects.create(user=self.regular, plugin=record)
        self.client.force_login(self.regular)
        self.assertEqual(self.client.get(self.url(record)).status_code, 404)
        self.assertEqual(self.client.get(self.url(record, download=True)).status_code, 404)

    def test_unknown_installed_package_shows_honest_unavailable_state(self):
        response = self.client.get(self.url(self.record))
        self.assertContains(response, "has no published SBOM")
        self.assertNotContains(response, "Download plugin SBOM")
        self.assertEqual(self.client.get(self.url(self.record, download=True)).status_code, 404)


class PluginUploadedInventoryTests(TestCase):
    def setUp(self):
        store_tests.PluginStoreTests.setUp(self)

    def upload(self, document=DOCUMENT):
        response = store_tests.PluginStoreTests.upload_zip(self, extra={"sbom.cdx.json": encoded(document)} if document is not None else None)
        self.assertEqual(response.status_code, 201)
        return PluginRecord.objects.get(plugin_id="zip.calculator")

    def url(self, record, *, download=False):
        return f"/plugins/{record.plugin_id}/{'sbom.json' if download else 'sbom/'}"

    def test_pending_other_users_upload_is_admin_only(self):
        record = self.upload()
        self.client.force_login(self.regular)
        for download in [False, True]:
            self.assertEqual(self.client.get(self.url(record, download=download)).status_code, 404)
        self.client.force_login(self.staff)
        response = self.client.get(self.url(record))
        self.assertContains(response, "Signed publisher SBOM")
        self.assertContains(response, "declared-helper")
        self.assertContains(response, "not been independently verified")
        self.assertNotContains(response, self.key.public_key)
        self.assertNotContains(response, self.key.fingerprint)
        self.assertNotContains(response, self.staff.username)
        self.assertNotContains(response, record.artifact.name)
        download = self.client.get(self.url(record, download=True))
        self.assertEqual(download.content, encoded())
        self.assertEqual(hashlib.sha256(download.content).hexdigest(), record.package_manifest["files"]["sbom.cdx.json"])
        self.assertEqual(json.loads(download.content)["components"][0]["name"], "declared-helper")

    def test_site_approved_upload_follows_available_catalog_visibility(self):
        record = self.upload()
        record.set_enabled(True)
        self.client.force_login(self.regular)
        self.assertEqual(self.client.get(self.url(record)).status_code, 200)
        self.assertEqual(self.client.get(self.url(record, download=True)).status_code, 200)

    def test_missing_publisher_sbom_produces_signed_files_only_inventory(self):
        record = self.upload(None)
        inventory = plugin_inventory(record)
        self.assertIn("file inventory only", inventory["scope"])
        document = json.loads(inventory["content"])
        self.assertEqual({component["type"] for component in document["components"]}, {"file"})
        self.assertEqual({component["name"] for component in document["components"]}, {"worker.js", "lib/calc.js", "plugin.json"})
        self.assertTrue(all(component["licenses"] == "Not declared" for component in inventory["components"]))

    def test_modified_archive_signed_hashes_and_revoked_keys_are_unavailable(self):
        record = self.upload()
        original = Path(record.artifact.path).read_bytes()
        Path(record.artifact.path).write_bytes(original + b"tampered")
        with self.assertRaises(InventoryUnavailable):
            plugin_inventory(record)
        Path(record.artifact.path).write_bytes(original)
        record.package_manifest["files"]["sbom.cdx.json"] = "0" * 64
        with self.assertRaises(InventoryUnavailable):
            plugin_inventory(record)
        record.refresh_from_db()
        from django.utils import timezone
        self.key.revoked_at = timezone.now()
        self.key.save()
        self.assertNotContains(self.client.get(self.url(record)), "Download plugin SBOM")
        self.assertEqual(self.client.get(self.url(record, download=True)).status_code, 404)

    def test_repacked_or_duplicate_sbom_members_still_require_the_signed_hash(self):
        record = self.upload()
        original = Path(record.artifact.path).read_bytes()
        for duplicate in [False, True]:
            stream = io.BytesIO()
            with ZipFile(io.BytesIO(original)) as source, ZipFile(stream, "w") as output:
                for member in source.infolist():
                    data = source.read(member)
                    if member.filename == "sbom.cdx.json" and not duplicate:
                        data = encoded({**DOCUMENT, "version": 2})
                    output.writestr(member.filename, data)
                if duplicate:
                    import warnings
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", UserWarning)
                        output.writestr("sbom.cdx.json", encoded())
            changed = stream.getvalue()
            Path(record.artifact.path).write_bytes(changed)
            # Even if a container-level hash is refreshed, the signed member
            # remains bound to its original bytes and unique archive location.
            record.content_hash = hashlib.sha256(changed).hexdigest()
            with self.subTest(duplicate=duplicate), self.assertRaises(InventoryUnavailable):
                plugin_inventory(record)

    def test_oversized_and_corrupt_sbom_reject_upload_without_installing_record(self):
        response = store_tests.PluginStoreTests.upload_zip(self, extra={"sbom.cdx.json": b' ' * (MAX_SBOM_BYTES + 1)})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(PluginRecord.objects.filter(plugin_id="zip.calculator").exists())
        response = store_tests.PluginStoreTests.upload_zip(self, extra={"sbom.cdx.json": encoded({**DOCUMENT, "dependencies": [{"ref": "app", "dependsOn": ["missing"]}]})})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(PluginRecord.objects.filter(plugin_id="zip.calculator").exists())

    def test_compressed_tar_inventory_handles_larger_normalized_private_zip(self):
        content = package(extra={"sbom.cdx.json": encoded(), "large-a.js": b"//" * 600000, "large-b.js": b"//" * 600000})
        with ZipFile(io.BytesIO(content)) as archive:
            files = {member.filename: archive.read(member) for member in archive.infolist()}
        hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        signature = {"format": "cadevil-plugin-signature-v1", "algorithm": "Ed25519", "key_id": self.key.fingerprint,
                     "signature": base64.b64encode(self.private.sign(canonical_payload(hashes))).decode()}
        files["signature.json"] = encoded(signature)
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            for name, data in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        response = self.client.post("/plugins/upload/", {"artifact": SimpleUploadedFile("plugin.tar.gz", stream.getvalue())}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 201)
        record = PluginRecord.objects.get(plugin_id="zip.calculator")
        self.assertGreater(Path(record.artifact.path).stat().st_size, 2 * 1024 * 1024)
        self.assertEqual(plugin_inventory(record)["source"], "Signed publisher SBOM")

    def test_publisher_component_text_is_escaped_and_never_loaded_as_external_content(self):
        document = copy.deepcopy(DOCUMENT)
        document["components"][0]["name"] = '<img src="/secret" onerror="alert(1)">'
        record = self.upload(document)
        response = self.client.get(self.url(record))
        self.assertContains(response, "&lt;img")
        self.assertNotContains(response, '<img src="/secret"')
        self.assertContains(response, "&quot;alert(1)&quot;")
