"""Distribution boundaries: declared files, metadata and derived WASM inventory."""
import base64
import csv
from hashlib import sha256
import io
import json
from pathlib import Path
import tempfile
import tarfile
import unittest
import zipfile

from scripts.check_build_artifacts import HOOK_INPUTS, MANIFEST, WASM, audit, audit_sdist, derived_sbom, reviewed_manifest


class BuildArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "source"
        self.root.mkdir()
        self.wheel = Path(self.temporary.name) / "package.whl"
        self.runtime = sorted(WASM | {"shared/__init__.py", "sbom/cadevil.cdx.json"})
        self.metadata = "cadevil_webapp-0.16.0.dist-info/"
        original = {"bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1,
                    "components": [{"type": "file", "bom-ref": name, "name": name} for name in sorted(WASM)],
                    "dependencies": [{"ref": name, "dependsOn": []} for name in sorted(WASM)]}
        for name in self.runtime:
            content = json.dumps(original).encode() if name.endswith(".cdx.json") else b"source"
            self.write(name, content)
        self.write(MANIFEST, json.dumps({"version": 1, "runtime": self.runtime, "build_only": []}).encode())
        self.write("pyproject.toml", b'[project]\nname="cadevil-webapp"\nversion="0.16.0"\ndescription="Fixture build"\nreadme="README.md"\nrequires-python=">=3.13"\ndependencies=["pytest>=8.4"]\n[project.entry-points."cadevil.plugins"]\ndemo="plugins.demo:plugin_manifest"\n')
        self.write("README.md", b"Fixture readme\n")
        self.write("LICENSE", b"MIT fixture\n")
        self.write("uv.lock", b"Fixture lock\n")
        self.write("hatch_build.py", b"Fixture hook\n")
        self.write("scripts/check_build_artifacts.py", b"Fixture validator\n")
        self.write("rust-toolchain.toml", b'[toolchain]\nchannel="1.98.1"\ntargets=["wasm32-unknown-unknown"]\n')
        for name in HOOK_INPUTS:
            if not (self.root / name).exists():
                self.write(name, b"Fixture build dependency\n")
        for folder in ("example_plugin", "rust_example_plugin"):
            for name in ("Cargo.toml", "Cargo.lock", "src/lib.rs", "build.rs"):
                self.write(f"plugins/{folder}/{name}", name.encode())
        required = {"pyproject.toml", "uv.lock", "LICENSE"} | WASM | {f"plugins/{folder}/{name}" for folder in ("example_plugin", "rust_example_plugin") for name in ("Cargo.toml", "Cargo.lock")}
        original["metadata"] = {"component": {"name": "cadevil-webapp", "version": "0.16.0", "type": "application"},
                                "properties": [{"name": "cadevil:input:sha256:" + name, "value": sha256((self.root / name).read_bytes()).hexdigest()} for name in sorted(required)]}
        self.write("sbom/cadevil.cdx.json", json.dumps(original).encode())
        payload = {name: (self.root / name).read_bytes() for name in self.runtime}
        for name in WASM:
            payload[name] = b"\x00asm\x01\x00\x00\x00"
        payload["sbom/cadevil.cdx.json"] = json.dumps(derived_sbom(original, {name: sha256(payload[name]).hexdigest() for name in WASM}, "1.98.1")).encode()
        evidence = {"rust_toolchain": "1.98.1", "rustc": "rustc 1.98.1 (48a229cea 2026-09-01)", "wasm_target": "wasm32-unknown-unknown",
                    "reviewed_manifest_sha256": sha256((self.root / MANIFEST).read_bytes()).hexdigest(),
                    "runtime_sha256": {name: sha256(content).hexdigest() for name, content in payload.items()},
                    "cargo_source_sha256": {f"plugins/{folder}/{name}": sha256((self.root / f"plugins/{folder}/{name}").read_bytes()).hexdigest()
                                            for folder in ("example_plugin", "rust_example_plugin") for name in ("Cargo.toml", "Cargo.lock", "src/lib.rs", "build.rs")},
                    "build_hook_sha256": {name: sha256((self.root / name).read_bytes()).hexdigest() for name in HOOK_INPUTS}}
        self.files = payload | {
            self.metadata + "METADATA": b"Metadata-Version: 2.4\nName: cadevil-webapp\nVersion: 0.16.0\nSummary: Fixture build\nLicense-File: LICENSE\nRequires-Python: >=3.13\nRequires-Dist: pytest>=8.4\nDescription-Content-Type: text/markdown\n\nFixture readme\n",
            self.metadata + "WHEEL": b"Wheel-Version: 1.0\nGenerator: hatchling 1.32.4\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n",
            self.metadata + "licenses/LICENSE": b"MIT fixture\n",
            self.metadata + "entry_points.txt": b"[cadevil.plugins]\ndemo = plugins.demo:plugin_manifest\n",
            self.metadata + "extra_metadata/cadevil-build.json": json.dumps(evidence).encode(),
        }

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def package(self):
        rows = [[name, "sha256=" + base64.urlsafe_b64encode(sha256(content).digest()).rstrip(b"=").decode(), str(len(content))] for name, content in sorted(self.files.items())]
        rows.append([self.metadata + "RECORD", "", ""])
        record = io.StringIO()
        csv.writer(record, lineterminator="\n").writerows(rows)
        with zipfile.ZipFile(self.wheel, "w") as archive:
            for name, content in self.files.items():
                archive.writestr(name, content)
            archive.writestr(self.metadata + "RECORD", record.getvalue())

    def reject(self, message):
        self.package()
        with self.assertRaisesRegex(ValueError, message):
            audit(self.wheel, self.root)

    def test_verified_payload_extracts_only_reviewed_files(self):
        self.package()
        destination = Path(self.temporary.name) / "runtime"
        report = audit(self.wheel, self.root, extract=destination)
        self.assertEqual(report["runtime_file_count"], len(self.runtime))
        self.assertEqual({path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()}, set(self.runtime))

    def test_report_cannot_overwrite_source_or_input_wheel(self):
        self.package()
        for destination, message in ((self.root / "README.md", "outside the source"),
                                     (self.wheel, "aliases an input artifact")):
            with self.subTest(destination=destination):
                original = destination.read_bytes()
                with self.assertRaisesRegex(ValueError, message):
                    audit(self.wheel, self.root, output=destination)
                self.assertEqual(destination.read_bytes(), original)

    def test_export_symlink_ancestor_cannot_reach_source(self):
        self.package()
        alias = Path(self.temporary.name) / "source-alias"
        alias.symlink_to(self.root, target_is_directory=True)
        for keyword in ("output", "extract"):
            with self.subTest(keyword=keyword):
                with self.assertRaisesRegex(ValueError, "outside the source"):
                    audit(self.wheel, self.root, **{keyword: alias / "unwritten"})
                self.assertFalse((self.root / "unwritten").exists())

    def test_report_and_runtime_destinations_cannot_overlap(self):
        self.package()
        destination = Path(self.temporary.name) / "new-output"
        for report, runtime in ((destination, destination), (destination / "report.json", destination),
                                (destination, destination / "runtime")):
            with self.subTest(report=report, runtime=runtime):
                with self.assertRaisesRegex(ValueError, "must be separate"):
                    audit(self.wheel, self.root, output=report, extract=runtime)
                self.assertFalse(destination.exists())

    def test_existing_unrelated_report_and_runtime_are_preserved(self):
        self.package()
        report = Path(self.temporary.name) / "existing-report.json"
        report.write_bytes(b"previous operator report")
        runtime = Path(self.temporary.name) / "existing-runtime"
        runtime.mkdir()
        retained = runtime / "operator-file"
        retained.write_bytes(b"retained operator output")
        for keyword, destination in (("output", report), ("extract", runtime)):
            with self.subTest(keyword=keyword):
                with self.assertRaisesRegex(ValueError, "must be new"):
                    audit(self.wheel, self.root, **{keyword: destination})
        self.assertEqual(report.read_bytes(), b"previous operator report")
        self.assertEqual(retained.read_bytes(), b"retained operator output")

    def test_unknown_private_file_is_rejected_even_with_valid_record(self):
        self.files[".env"] = b"private-canary"
        self.reject("Unreviewed wheel payload")

    def test_unreviewed_rust_source_is_rejected_even_with_valid_record(self):
        self.files["plugins/example_plugin/src/lib.rs"] = b"source"
        self.reject("Unreviewed wheel payload")

    def test_project_source_tampering_is_rejected(self):
        self.files["shared/__init__.py"] = b"changed source"
        self.reject("source hash differs")

    def test_wheel_identity_must_match_pyproject(self):
        self.files[self.metadata + "METADATA"] = self.files[self.metadata + "METADATA"].replace(b"Version: 0.16.0", b"Version: 9.0.0")
        self.reject("project identity")

    def test_dependency_injection_is_rejected(self):
        self.files[self.metadata + "METADATA"] = self.files[self.metadata + "METADATA"].replace(b"Description-Content-Type:", b"Requires-Dist: unwanted>=1\nDescription-Content-Type:")
        self.reject("dependencies differ")

    def test_entrypoint_injection_is_rejected(self):
        self.files[self.metadata + "entry_points.txt"] += b"extra = private.module:run\n"
        self.reject("entry points")

    def test_entrypoint_comments_cannot_carry_private_text(self):
        self.files[self.metadata + "entry_points.txt"] += b"# private-canary\n"
        self.reject("entry points")

    def test_platform_wheel_mislabel_is_rejected(self):
        self.files[self.metadata + "WHEEL"] = self.files[self.metadata + "WHEEL"].replace(b"true", b"false")
        self.reject("portable Python")

    def test_cargo_source_evidence_must_match_input(self):
        self.write("plugins/example_plugin/src/lib.rs", b"new source")
        self.reject("reviewed source inputs")

    def test_manifest_evidence_must_match_reviewed_list(self):
        self.write(MANIFEST, json.dumps({"version": 1, "runtime": self.runtime, "build_only": ["README.md"]}).encode())
        self.reject("reviewed source inputs")

    def test_derived_sbom_cannot_inject_components_or_dependencies(self):
        name = "sbom/cadevil.cdx.json"
        bom = json.loads(self.files[name])
        bom["components"].append({"type": "library", "name": "injected", "version": "1"})
        self.files[name] = json.dumps(bom).encode()
        self.reject("unreviewed inventory")

    def test_duplicate_derived_sbom_metadata_is_rejected_with_valid_record(self):
        name = "sbom/cadevil.cdx.json"
        self.files[name] = b'{"metadata":"unreviewed duplicate",' + self.files[name][1:]
        evidence_name = self.metadata + "extra_metadata/cadevil-build.json"
        evidence = json.loads(self.files[evidence_name])
        evidence["runtime_sha256"][name] = sha256(self.files[name]).hexdigest()
        self.files[evidence_name] = json.dumps(evidence).encode()
        self.reject("Duplicate JSON key: metadata")

    def test_duplicate_build_evidence_is_rejected_with_valid_record(self):
        name = self.metadata + "extra_metadata/cadevil-build.json"
        self.files[name] = b'{"rustc":"unreviewed duplicate",' + self.files[name][1:]
        self.reject("Duplicate JSON key: rustc")

    def test_nonfinite_build_evidence_is_rejected(self):
        name = self.metadata + "extra_metadata/cadevil-build.json"
        self.files[name] = self.files[name].replace(b'"1.98.1"', b'NaN', 1)
        self.reject("Nonfinite JSON number: NaN")

    def test_duplicate_manifest_and_source_sbom_are_rejected(self):
        for name, key in ((MANIFEST, "version"), ("sbom/cadevil.cdx.json", "metadata")):
            with self.subTest(name=name):
                original = (self.root / name).read_bytes()
                self.write(name, ('{"' + key + '":"unreviewed duplicate",').encode() + original[1:])
                self.reject("Duplicate JSON key: " + key)
                self.write(name, original)

    def test_symlink_source_is_rejected(self):
        path = self.root / "shared/__init__.py"
        path.unlink()
        path.symlink_to(self.root / "pyproject.toml")
        self.reject("Symlink")

    def test_runtime_allowlist_cannot_declare_development_code(self):
        self.write(MANIFEST, json.dumps({"version": 1, "runtime": ["plugins/development_mcp/native.py"], "build_only": []}).encode())
        with self.assertRaisesRegex(ValueError, "Private/development"):
            reviewed_manifest(self.root)

    def test_existing_extract_destination_is_preserved(self):
        self.package()
        destination = Path(self.temporary.name) / "runtime"
        destination.mkdir()
        marker = destination / "existing.txt"
        marker.write_text("preserved")
        with self.assertRaisesRegex(ValueError, "destination must be new"):
            audit(self.wheel, self.root, extract=destination)
        self.assertEqual(marker.read_text(), "preserved")

    def test_private_text_cannot_be_injected_into_readme_metadata(self):
        self.files[self.metadata + "METADATA"] += b"private-description-canary\n"
        self.reject("description/readme")

    def test_private_text_cannot_be_injected_into_license_metadata(self):
        self.files[self.metadata + "licenses/LICENSE"] += b"private-license-canary\n"
        self.reject("license differs")

    def test_duplicate_identity_header_is_rejected(self):
        self.files[self.metadata + "METADATA"] = self.files[self.metadata + "METADATA"].replace(b"Name:", b"Name: duplicate\nName:")
        self.reject("duplicate identity")

    def test_legacy_build_helper_source_is_bound(self):
        self.write("plugins/example_plugin/build.rs", b"changed builder source")
        self.reject("reviewed source inputs")

    def test_stale_source_bom_version_is_rejected(self):
        name = "sbom/cadevil.cdx.json"
        bom = json.loads((self.root / name).read_text())
        bom["metadata"]["component"]["version"] = "0.15.1"
        self.write(name, json.dumps(bom).encode())
        self.reject("Source SBOM application identity")

    def test_stale_source_lock_evidence_is_rejected(self):
        self.write("uv.lock", b"changed lock")
        self.reject("Source SBOM input evidence is stale")

    def test_core_metadata_version_is_pinned(self):
        self.files[self.metadata + "METADATA"] = self.files[self.metadata + "METADATA"].replace(b"Metadata-Version: 2.4", b"Metadata-Version: private-canary")
        self.reject("core metadata version")

    def test_metadata_root_is_bound_to_project(self):
        previous = self.metadata
        self.metadata = "private-canary-9.0.dist-info/"
        self.files = {name.replace(previous, self.metadata): content for name, content in self.files.items()}
        self.reject("metadata root differs")

    def test_source_archive_metadata_cannot_carry_private_text(self):
        self.package()
        sdist = Path(self.temporary.name) / "package.tar.gz"
        contents = {name: (self.root / name).read_bytes() for name in self.runtime}
        contents["PKG-INFO"] = self.files[self.metadata + "METADATA"] + b"private-canary\n"
        with tarfile.open(sdist, "w:gz") as archive:
            for name, content in contents.items():
                member = tarfile.TarInfo("cadevil_webapp-0.16.0/" + name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        with self.assertRaisesRegex(ValueError, "Source archive metadata differs"):
            audit(self.wheel, self.root, sdist=sdist)

    def test_source_archive_root_is_bound_to_project(self):
        self.package()
        sdist = Path(self.temporary.name) / "package.tar.gz"
        with tarfile.open(sdist, "w:gz") as archive:
            member = tarfile.TarInfo("private-canary-9.0/PKG-INFO")
            content = self.files[self.metadata + "METADATA"]
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        with self.assertRaisesRegex(ValueError, "Source archive root differs"):
            audit(self.wheel, self.root, sdist=sdist)
