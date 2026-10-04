"""Real source fixtures exercise the shared checkout/build guards without Django."""
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("source_checks", ROOT / "scripts/check_source.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


class SourceCheckTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="cadevil source checks ")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.manifest = json.loads((ROOT / checks.MANIFEST).read_text())
        for value in self.manifest["runtime"] + self.manifest["build_only"]:
            target = self.root / value
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / value, target)

    def change_bom(self, change):
        path = self.root / "sbom/cadevil.cdx.json"
        document = json.loads(path.read_text())
        change(document)
        path.write_text(json.dumps(document))

    def add_input(self, value, content=""):
        self.manifest["runtime"].append(value)
        path = self.root / value
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        (self.root / checks.MANIFEST).write_text(json.dumps(self.manifest))
        context = checks._load_helper("docker/context.py")
        (self.root / ".dockerignore").write_text(context.dockerignore(self.manifest))

    def received(self):
        for value in checks.BUILD_CONTROLS:
            (self.root / value).unlink()

    def test_clean_checkout_checks_all_reviewed_inputs_and_does_not_write_caches(self):
        # Unknown checkout files remain excluded from the reviewed package.
        private = self.root / "data/private.key"
        private.parent.mkdir()
        private.write_text("PRIVATE_CANARY_DO_NOT_PACKAGE")
        before = {path.relative_to(self.root).as_posix(): sha256(path.read_bytes()).hexdigest()
                  for path in self.root.rglob("*") if path.is_file()}
        report = checks.validate_source(self.root)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["reviewed_input_count"], len(self.manifest["runtime"]) + len(self.manifest["build_only"]))
        self.assertEqual(report["compiler_copy_input_count"], report["reviewed_input_count"] - 2)
        self.assertEqual(report["plugin_owned_bim_isolation"], "passed")
        self.assertEqual(report["runtime_python_files_parsed"], sum(Path(value).suffix == ".py" for value in self.manifest["runtime"]))
        self.assertGreater(report["sbom_dependency_edge_count"], 0)
        self.assertEqual(before, {path.relative_to(self.root).as_posix(): sha256(path.read_bytes()).hexdigest()
                                  for path in self.root.rglob("*") if path.is_file()})
        self.assertFalse(list(self.root.rglob("__pycache__")))

    def test_exact_received_source_passes_without_build_controls(self):
        self.received()
        report = checks.validate_source(self.root, received=True)
        self.assertTrue(report["received_context"])
        self.assertEqual(report["compiler_copy_layout"], "checked_before_transmission")
        self.assertEqual(report["reviewed_input_count"], len(self.manifest["runtime"]) + len(self.manifest["build_only"]))

    def test_received_source_rejects_one_unknown_private_file(self):
        self.received()
        (self.root / "private.txt").write_text("PRIVATE_CANARY_DO_NOT_PACKAGE")
        with self.assertRaisesRegex(ValueError, "Received Docker source"):
            checks.validate_source(self.root, received=True)

    def test_received_source_rejects_one_missing_required_file(self):
        self.received()
        (self.root / "plugins/example_plugin/src/lib.rs").unlink()
        with self.assertRaisesRegex(ValueError, "Received Docker source"):
            checks.validate_source(self.root, received=True)

    def test_stale_source_evidence_is_rejected(self):
        with (self.root / "plugins/example_plugin/Cargo.lock").open("a") as handle:
            handle.write("\n# changed after inventory generation\n")
        with self.assertRaisesRegex(ValueError, "Source SBOM input evidence is stale"):
            checks.validate_source(self.root)

    def test_wrong_source_sbom_identity_is_rejected(self):
        self.change_bom(lambda document: document["metadata"]["component"].update(version="0.0.0"))
        with self.assertRaisesRegex(ValueError, "application identity"):
            checks.validate_source(self.root)

    def test_invalid_syntax_in_owned_plugin_code_is_rejected_without_importing_it(self):
        value = "plugins/bim_model_manager/resources/__init__.py"
        (self.root / value).write_text("def broken(:\n")
        with self.assertRaises(SyntaxError) as failure:
            checks.validate_source(self.root)
        self.assertEqual(failure.exception.filename, value)
        command = [sys.executable, "-E", "-B", str(self.root / "scripts/check_source.py"), "--source", str(self.root)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid syntax", result.stderr)
        self.assertIn("Source check failed: " + value + ":1:", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_missing_compiler_copy_is_rejected_at_the_uv_build_boundary(self):
        path = self.root / "Dockerfile"
        path.write_text(path.read_text().replace("COPY scripts/browser-pki/package.json scripts/browser-pki/package-lock.json ./scripts/browser-pki/\n", ""))
        with self.assertRaisesRegex(ValueError, "Compiler COPY omits.*scripts/browser-pki/package"):
            checks.validate_source(self.root)

    def test_copy_after_uv_build_cannot_repair_an_omitted_input(self):
        path = self.root / "Dockerfile"
        instruction = "COPY scripts/browser-pki/package.json scripts/browser-pki/package-lock.json ./scripts/browser-pki/\n"
        content = path.read_text().replace(instruction, "")
        content = content.replace("# Export the exact wheel", instruction + "\n# Export the exact wheel")
        path.write_text(content)
        with self.assertRaisesRegex(ValueError, "before the compiler uv build gate"):
            checks.validate_source(self.root)

    def test_compiler_copy_rejects_a_path_changing_destination(self):
        path = self.root / "Dockerfile"
        path.write_text(path.read_text().replace("COPY shared/ ./shared/", "COPY shared/ ./mycelium/"))
        with self.assertRaisesRegex(ValueError, "COPY changes reviewed input path"):
            checks.validate_source(self.root)

    def test_stage_copy_cannot_smuggle_files_into_received_source_through_parent_segments(self):
        path = self.root / "Dockerfile"
        original = path.read_text()
        for destination, error in (("/tmp/../build", "canonical absolute paths"),
                                   ("/tmp//build", "canonical absolute paths"),
                                   ("/tmp/./build", "canonical absolute paths"),
                                   ("/tmp//", "canonical absolute paths"),
                                   ("//", "canonical absolute paths"),
                                   ("/tmp/*", "canonical absolute paths"),
                                   ("/tmp/uv\x01", "canonical absolute paths"),
                                   ("/build/tools", "Unreviewed stage COPY")):
            with self.subTest(destination=destination):
                path.write_text(original.replace("COPY --from=uv /uv /usr/local/bin/uv", "COPY --from=uv /uv " + destination))
                with self.assertRaisesRegex(ValueError, error):
                    checks.validate_source(self.root)

    def test_broad_copy_add_and_unsupported_copy_flags_are_rejected(self):
        path = self.root / "Dockerfile"
        original = path.read_text()
        for instruction, error in (("COPY . ./", "Unreviewed or broad"),
                                   ("ADD shared/ ./shared/", "Docker ADD"),
                                   ("COPY --chmod=0644 shared/ ./shared/", "Unsupported Docker COPY flags")):
            with self.subTest(instruction=instruction):
                path.write_text(original.replace("COPY shared/ ./shared/", instruction))
                with self.assertRaisesRegex(ValueError, error):
                    checks.validate_source(self.root)

    def test_private_input_cannot_be_added_to_the_manifest(self):
        self.add_input("mcp_tools/provider.py", "# development only\n")
        with self.assertRaisesRegex(ValueError, "Development tool environments"):
            checks.validate_source(self.root)

    def test_declared_input_symlink_is_rejected(self):
        path = self.root / "shared/models.py"
        path.unlink()
        path.symlink_to(ROOT / "shared/models.py")
        with self.assertRaisesRegex(ValueError, "[Ss]ymlink"):
            checks.validate_source(self.root)

    def test_declared_input_symlink_ancestor_is_rejected(self):
        directory = self.root / "shared"
        shutil.rmtree(directory)
        directory.symlink_to(ROOT / "shared", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "[Ss]ymlink"):
            checks.validate_source(self.root)

    def test_symlinked_helpers_are_rejected_before_their_code_can_execute(self):
        with tempfile.TemporaryDirectory(prefix="untrusted helper ") as outside:
            marker = Path(outside) / "EXECUTED"
            payload = Path(outside) / "helper.py"
            payload.write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('side effect')\n")
            command = [sys.executable, "-I", "-B", str(self.root / "scripts/check_source.py"), "--source", str(self.root)]
            for value in ("scripts/check_build_artifacts.py", "docker/context.py"):
                with self.subTest(helper=value):
                    helper = self.root / value
                    helper.unlink()
                    helper.symlink_to(payload)
                    result = subprocess.run(command, capture_output=True, text=True, check=False)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("Symlink in source-check helper: " + value, result.stderr)
                    self.assertFalse(marker.exists())
                    helper.unlink()
                    shutil.copyfile(ROOT / value, helper)

    def test_helper_directory_symlink_is_rejected_before_context_code_can_execute(self):
        with tempfile.TemporaryDirectory(prefix="untrusted helper directory ") as outside:
            directory = Path(outside)
            marker = directory / "EXECUTED"
            (directory / "context.py").write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('side effect')\n")
            shutil.rmtree(self.root / "docker")
            (self.root / "docker").symlink_to(directory, target_is_directory=True)
            command = [sys.executable, "-I", "-B", str(self.root / "scripts/check_source.py"), "--source", str(self.root)]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Symlink in source-check helper: docker/context.py", result.stderr)
            self.assertFalse(marker.exists())

    def test_shared_forwarders_and_legacy_models_are_rejected(self):
        path = self.root / "shared/models.py"
        original = path.read_text()
        for code, error in (("from plugins.bim_model_manager.django.models import BuildingMetrics\n", "imports plugin-owned"),
                            ("import apps.plugins.bim_model_manager.django.models\n", "imports plugin-owned"),
                            ("FORWARD = 'plugins.bim_model_manager.django.models'\n", "compatibility forwarding"),
                            ("class BuildingMetrics: pass\n", "declares a legacy BIM model"),
                            ("MODEL = 'shared.buildingmetrics'\n", "legacy BIM model reference")):
            with self.subTest(code=code):
                path.write_text(original + "\n" + code)
                with self.assertRaisesRegex(ValueError, error):
                    checks.validate_source(self.root)

    def test_historical_bim_migration_is_rejected(self):
        self.add_input("plugins/bim_model_manager/django/migrations/0002_old.py", "# historical schema\n")
        with self.assertRaisesRegex(ValueError, "Historical application migration"):
            checks.validate_source(self.root)

    def test_old_shared_bim_service_is_rejected(self):
        self.add_input("shared/ifc_viewer.py", "# old shared service\n")
        with self.assertRaisesRegex(ValueError, "implementation remains in shared"):
            checks.validate_source(self.root)

    def test_sbom_graph_rejects_duplicate_components_dependencies_and_dangling_targets(self):
        path = self.root / "sbom/cadevil.cdx.json"
        original = path.read_text()
        mutations = (
            (lambda document: document["components"].append(document["components"][0]), "component references.*duplicated"),
            (lambda document: document["dependencies"].append(document["dependencies"][0]), "dependency references.*duplicated"),
            (lambda document: document["dependencies"][0]["dependsOn"].append("urn:missing"), "targets.*dangling"),
            (lambda document: document["dependencies"].pop(), "explicit dependency entries"),
        )
        for change, error in mutations:
            with self.subTest(error=error):
                path.write_text(original)
                self.change_bom(change)
                with self.assertRaisesRegex(ValueError, error):
                    checks.validate_source(self.root)

    def test_duplicate_json_keys_cannot_hide_an_input_list(self):
        path = self.root / checks.MANIFEST
        content = path.read_text().rstrip()
        path.write_text(content[:-1] + ', "runtime": []}')
        with self.assertRaisesRegex(ValueError, "Duplicate JSON key: runtime"):
            checks.validate_source(self.root)

    def test_cli_emits_compact_report_or_actionable_error_without_cache_writes(self):
        command = [sys.executable, "-E", "-B", str(self.root / "scripts/check_source.py"), "--source", str(self.root)]
        passed = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertEqual(json.loads(passed.stdout)["status"], "passed")
        self.assertEqual(passed.stderr, "")
        (self.root / "shared/models.py").unlink()
        rejected = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(rejected.returncode, 1)
        self.assertEqual(rejected.stdout, "")
        self.assertIn("Source check failed:", rejected.stderr)
        self.assertIn("shared/models.py", rejected.stderr)
        self.assertFalse(list(self.root.rglob("__pycache__")))


if __name__ == "__main__":
    unittest.main()
