"""Hatch hook: exact reviewed files and isolated Rust WebAssembly builds."""
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import types

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


def load_helper(root, relative, name):
    current = Path(root)
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Symlink in build helper: " + relative)
    if not current.is_file():
        raise ValueError("Missing regular build helper: " + relative)
    module = types.ModuleType(name)
    module.__file__ = str(current)
    exec(compile(current.read_bytes(), str(current), "exec"), module.__dict__)
    return module


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        # Editable installs serve the current checkout through Hatch's .pth;
        # dependency sync and test setup never compile the Rust assets.
        if self.target_name == "wheel" and version == "editable":
            return
        sys.dont_write_bytecode = True
        root = Path(self.root)
        checks = load_helper(root, "scripts/check_build_artifacts.py", "cadevil_build_checks")
        self._checks = checks
        source_checks = load_helper(root, "scripts/check_source.py", "cadevil_source_checks")
        received = os.environ.get("CADEVIL_BUILD_RECEIVED_CONTEXT") == "1"
        report = source_checks.validate_source(root, received=received)
        print(f"Verified {report['reviewed_input_count']} source inputs before {self.target_name} build.", file=sys.stderr)
        manifest = checks.reviewed_manifest(root)
        self._source_hashes = {name: sha256(checks.regular_file(root, name).read_bytes()).hexdigest()
                               for name in manifest["runtime"] + manifest["build_only"]
                               if not received or name not in {"Dockerfile", ".dockerignore"}}
        paths = list(manifest["runtime"])
        if self.target_name == "sdist":
            paths += manifest["build_only"]
        mapping = {str(checks.regular_file(root, name)): name for name in paths}
        if self.target_name == "wheel":
            self._temporary = tempfile.TemporaryDirectory(prefix="cadevil-wheel-")
            temporary = Path(self._temporary.name)
            channel = tomllib.loads((root / "rust-toolchain.toml").read_text())["toolchain"]["channel"]
            target = "wasm32-unknown-unknown"
            toolchain = channel
            probe = subprocess.run(["rustup", "run", channel, "rustc", "--version"], text=True, capture_output=True)
            if probe.returncode:
                # A developer's already-installed stable alias may have exactly
                # the pinned compiler; verify its identity before accepting it.
                stable = subprocess.run(["rustup", "run", "stable", "rustc", "--version"], text=True, capture_output=True)
                if stable.returncode == 0 and stable.stdout.startswith("rustc " + channel + " "):
                    toolchain, probe = "stable", stable
                else:
                    subprocess.run(["rustup", "toolchain", "install", channel, "--profile", "minimal"], check=True)
                    probe = subprocess.run(["rustup", "run", channel, "rustc", "--version"], check=True, text=True, capture_output=True)
            if not probe.stdout.startswith("rustc " + channel + " "):
                raise ValueError("Rust compiler differs from the pinned release")
            installed = subprocess.check_output(["rustup", "target", "list", "--installed", "--toolchain", toolchain], text=True).splitlines()
            if target not in installed:
                subprocess.run(["rustup", "target", "add", "--toolchain", toolchain, target], check=True)
            source_hashes = {}
            outputs = {}
            project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
            checks.validate_source_sbom(checks.strict_json((root / "sbom/cadevil.cdx.json").read_text()), root, project)
            for folder, destination in (
                ("example_plugin", "plugins/example_plugin/static/wasm/example_plugin.wasm"),
                ("rust_example_plugin", "resources/static/wasm/rust_example_plugin.wasm"),
            ):
                crate = root / "plugins" / folder
                isolated_crate = temporary / folder / "source"
                target_directory = temporary / folder / "target"
                # Cargo cannot discover unreviewed workspace/config/source
                # neighbours when the crate inputs are copied into isolation.
                for relative in ("Cargo.toml", "Cargo.lock", "src/lib.rs", "build.rs"):
                    name = (crate / relative).relative_to(root).as_posix()
                    content = checks.regular_file(root, name).read_bytes()
                    destination_file = isolated_crate / relative
                    destination_file.parent.mkdir(parents=True, exist_ok=True)
                    destination_file.write_bytes(content)
                    source_hashes[name] = sha256(content).hexdigest()
                subprocess.run(["rustup", "run", toolchain, "cargo", "build", "--locked", "--release", "--target", target,
                                "--lib", "--manifest-path", str(isolated_crate / "Cargo.toml"), "--target-dir", str(target_directory)], cwd=isolated_crate, check=True)
                artifact = target_directory / target / "release" / (folder + ".wasm")
                if not artifact.read_bytes().startswith(b"\x00asm\x01\x00\x00\x00"):
                    raise ValueError("Rust build did not produce a valid WebAssembly module")
                outputs[destination] = artifact
            bom_name = "sbom/cadevil.cdx.json"
            bom = checks.derived_sbom(checks.strict_json((root / bom_name).read_text()),
                                      {name: sha256(path.read_bytes()).hexdigest() for name, path in outputs.items()}, channel)
            derived_bom = temporary / "cadevil.cdx.json"
            derived_bom.write_text(json.dumps(bom, sort_keys=True, indent=2) + "\n")
            outputs[bom_name] = derived_bom
            mapping = {str(outputs.get(name, checks.regular_file(root, name))): name for name in paths}
            evidence = {"rust_toolchain": channel, "rustc": probe.stdout.strip(), "wasm_target": target,
                        "cargo_source_sha256": source_hashes,
                        "build_hook_sha256": {name: sha256(checks.regular_file(root, name).read_bytes()).hexdigest() for name in checks.HOOK_INPUTS},
                        "reviewed_manifest_sha256": sha256((root / checks.MANIFEST).read_bytes()).hexdigest(),
                        "runtime_sha256": {name: sha256(Path(source).read_bytes()).hexdigest() for source, name in mapping.items()}}
            evidence_path = temporary / "cadevil-build.json"
            evidence_path.write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n")
            build_data["extra_metadata"] = {str(evidence_path): "cadevil-build.json"}
        build_data["force_include"] = mapping

    def finalize(self, version, build_data, artifact_path):
        if self.target_name == "wheel" and version == "editable":
            return
        temporary = getattr(self, "_temporary", None)
        artifact = Path(artifact_path)
        checks = self._checks
        root = Path(self.root)
        project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
        stem = project["name"].replace("-", "_") + "-" + project["version"]
        expected_name = stem + ("-py3-none-any.whl" if self.target_name == "wheel" else ".tar.gz")
        try:
            if artifact.is_symlink() or not artifact.is_file() or artifact.name != expected_name:
                raise ValueError("Unexpected build artifact path or type")
            for name, digest in self._source_hashes.items():
                if sha256(checks.regular_file(root, name).read_bytes()).hexdigest() != digest:
                    raise ValueError("Source input changed during build: " + name)
            if self.target_name == "wheel":
                report = checks.audit(artifact, root, output=os.environ.get("CADEVIL_BUILD_REPORT"),
                                      extract=os.environ.get("CADEVIL_BUILD_RUNTIME_DIRECTORY"))
                count = report["runtime_file_count"]
            elif self.target_name == "sdist":
                report = checks.audit_sdist(artifact, root)
                count = report["sdist_reviewed_file_count"]
            else:
                raise ValueError("Unsupported checked build target")
            print(f"Verified {artifact.name}: {count} reviewed files; source inputs unchanged.", file=sys.stderr)
        except Exception:
            # This exact regular artifact was just produced by the current
            # target. Failed distributions must not look like usable output.
            if artifact.name == expected_name and artifact.is_file() and not artifact.is_symlink():
                artifact.unlink()
            raise
        finally:
            if temporary is not None:
                temporary.cleanup()
