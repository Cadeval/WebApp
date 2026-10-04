"""Build lifecycle gates reject bad output before it becomes a usable artifact."""
from hashlib import sha256
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from scripts import check_build_artifacts as checks
from tests import test_build_artifacts

ROOT = Path(__file__).resolve().parents[1]


class BuildHookTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_build_artifacts.BuildArtifactTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        # Exercise our lifecycle independently of the build frontend. An
        # actual uv build separately verifies the backend's real integration.
        interface = ModuleType("hatchling.builders.hooks.plugin.interface")
        interface.BuildHookInterface = type("BuildHookInterface", (), {})
        spec = importlib.util.spec_from_file_location("tested_cadevil_hook", ROOT / "hatch_build.py")
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"hatchling.builders.hooks.plugin.interface": interface}):
            spec.loader.exec_module(self.module)
        self.hook = self.module.CustomBuildHook()
        self.hook.root = str(self.fixture.root)
        self.hook.target_name = "wheel"
        self.hook._checks = checks
        self.hook._source_hashes = {name: sha256((self.fixture.root / name).read_bytes()).hexdigest()
                                    for name in self.fixture.runtime}
        self.hook._temporary = Mock()
        self.artifact = Path(self.fixture.temporary.name) / "cadevil_webapp-0.16.0-py3-none-any.whl"

    def wheel(self):
        self.fixture.package()
        self.fixture.wheel.replace(self.artifact)
        return self.artifact

    def sdist(self, *, extra=None, metadata=None):
        self.hook.target_name = "sdist"
        self.artifact = Path(self.fixture.temporary.name) / "cadevil_webapp-0.16.0.tar.gz"
        content = {name: (self.fixture.root / name).read_bytes() for name in self.fixture.runtime}
        content["PKG-INFO"] = metadata or self.fixture.files[self.fixture.metadata + "METADATA"]
        content.update(extra or {})
        with tarfile.open(self.artifact, "w:gz") as archive:
            for name, value in content.items():
                member = tarfile.TarInfo("cadevil_webapp-0.16.0/" + name)
                member.size = len(value)
                archive.addfile(member, io.BytesIO(value))
        return self.artifact

    def test_finalize_automatically_validates_completed_wheel(self):
        self.wheel()
        with patch.object(checks, "audit", wraps=checks.audit) as audit:
            self.hook.finalize("standard", {}, str(self.artifact))
        audit.assert_called_once()
        self.assertTrue(self.artifact.is_file())
        self.hook._temporary.cleanup.assert_called_once()

    def test_bad_wheel_is_removed_and_unrelated_output_preserved(self):
        self.fixture.files[".env"] = b"PRIVATE_CANARY_DO_NOT_PACKAGE"
        self.wheel()
        unrelated = self.artifact.with_name("unrelated.txt")
        unrelated.write_text("retained user output")
        with self.assertRaisesRegex(ValueError, "Unreviewed wheel payload"):
            self.hook.finalize("standard", {}, str(self.artifact))
        self.assertFalse(self.artifact.exists())
        self.assertEqual(unrelated.read_text(), "retained user output")
        self.hook._temporary.cleanup.assert_called_once()

    def test_input_drift_during_build_rejects_artifact(self):
        self.wheel()
        self.fixture.write("shared/__init__.py", b"source changed during build")
        with self.assertRaisesRegex(ValueError, "Source input changed during build"):
            self.hook.finalize("standard", {}, str(self.artifact))
        self.assertFalse(self.artifact.exists())
        self.hook._temporary.cleanup.assert_called_once()

    def test_docker_runtime_export_and_report_follow_successful_validation(self):
        self.wheel()
        runtime = self.artifact.parent / "runtime"
        report = self.artifact.parent / "artifact-report.json"
        with patch.dict("os.environ", {"CADEVIL_BUILD_RUNTIME_DIRECTORY": str(runtime), "CADEVIL_BUILD_REPORT": str(report)}):
            self.hook.finalize("standard", {}, str(self.artifact))
        record = json.loads(report.read_text())
        self.assertEqual(record["status"], "passed")
        self.assertEqual(record["wheel_sha256"], sha256(self.artifact.read_bytes()).hexdigest())
        self.assertEqual({path.relative_to(runtime).as_posix() for path in runtime.rglob("*") if path.is_file()}, set(self.fixture.runtime))

    def test_bad_wheel_does_not_export_runtime_or_publish_passing_report(self):
        self.fixture.files[".env"] = b"PRIVATE_CANARY_DO_NOT_PACKAGE"
        self.wheel()
        runtime = self.artifact.parent / "runtime"
        report = self.artifact.parent / "artifact-report.json"
        with patch.dict("os.environ", {"CADEVIL_BUILD_RUNTIME_DIRECTORY": str(runtime), "CADEVIL_BUILD_REPORT": str(report)}):
            with self.assertRaisesRegex(ValueError, "Unreviewed wheel payload"):
                self.hook.finalize("standard", {}, str(self.artifact))
        self.assertFalse(runtime.exists())
        self.assertFalse(report.exists())
        self.assertFalse(self.artifact.exists())

    def test_source_only_build_is_validated_without_any_wheel(self):
        self.sdist()
        self.assertFalse(self.fixture.wheel.exists())
        self.hook.finalize("standard", {}, str(self.artifact))
        self.assertTrue(self.artifact.is_file())

    def test_source_only_build_rejects_unlisted_private_input(self):
        self.sdist(extra={".env": b"PRIVATE_CANARY_DO_NOT_PACKAGE"})
        with self.assertRaisesRegex(ValueError, "Unreviewed/duplicate source archive input"):
            self.hook.finalize("standard", {}, str(self.artifact))
        self.assertFalse(self.artifact.exists())

    def test_source_only_build_rejects_injected_metadata(self):
        self.sdist(metadata=self.fixture.files[self.fixture.metadata + "METADATA"] + b"private-description-canary")
        with self.assertRaisesRegex(ValueError, "description/readme"):
            self.hook.finalize("standard", {}, str(self.artifact))
        self.assertFalse(self.artifact.exists())

    def test_failed_source_guard_runs_before_compiler_or_output(self):
        self.fixture.write("scripts/check_build_artifacts.py", (ROOT / "scripts/check_build_artifacts.py").read_bytes())
        self.fixture.write("scripts/check_source.py", b"def validate_source(*args, **kwargs):\n    raise ValueError('source guard rejected checkout')\n")
        with patch.object(self.module.subprocess, "run") as compiler:
            with self.assertRaisesRegex(ValueError, "source guard rejected checkout"):
                self.hook.initialize("standard", {})
        compiler.assert_not_called()
        self.assertFalse(self.artifact.exists())

    def test_editable_dependency_install_does_not_compile_or_audit_distribution(self):
        with patch.object(checks, "audit") as audit, patch.object(self.module.subprocess, "run") as compiler:
            self.hook.initialize("editable", {})
            self.hook.finalize("editable", {}, "unused.pth")
        audit.assert_not_called()
        compiler.assert_not_called()

    def test_helper_symlink_is_rejected_before_its_code_executes(self):
        marker = Path(self.fixture.temporary.name) / "executed"
        malicious = Path(self.fixture.temporary.name) / "malicious.py"
        malicious.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n")
        helper = self.fixture.root / "scripts/check_source.py"
        helper.unlink()
        helper.symlink_to(malicious)
        with self.assertRaisesRegex(ValueError, "Symlink in build helper"):
            self.module.load_helper(self.fixture.root, "scripts/check_source.py", "never_executed")
        self.assertFalse(marker.exists())

    def test_helper_directory_symlink_is_rejected_before_execution(self):
        marker = Path(self.fixture.temporary.name) / "executed"
        outside = Path(self.fixture.temporary.name) / "outside-helpers"
        outside.mkdir()
        (outside / "helper.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
        (self.fixture.root / "aliased-helpers").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symlink in build helper"):
            self.module.load_helper(self.fixture.root, "aliased-helpers/helper.py", "never_executed")
        self.assertFalse(marker.exists())

    def test_regular_helper_load_does_not_write_bytecode(self):
        self.fixture.write("scripts/safe_helper.py", b"answer = 42\n")
        helper = self.module.load_helper(self.fixture.root, "scripts/safe_helper.py", "safe_helper")
        self.assertEqual(helper.answer, 42)
        self.assertEqual(list(self.fixture.root.rglob("__pycache__")), [])


if __name__ == "__main__":
    unittest.main()
