import json
from pathlib import Path

from django.test import SimpleTestCase


class WasmBuildMigrationTests(SimpleTestCase):
    project_root = Path(__file__).resolve().parents[2]

    def test_javascript_builders_are_replaced_by_rust_binaries(self) -> None:
        for plugin_name in ("example_plugin", "rust_example_plugin"):
            with self.subTest(plugin_name=plugin_name):
                plugin_root = self.project_root / "src" / plugin_name
                self.assertFalse((plugin_root / "build_wasm.mjs").exists())
                self.assertTrue((plugin_root / "build.rs").is_file())
                manifest = (plugin_root / "Cargo.toml").read_text(encoding="utf-8")
                self.assertIn("build = false", manifest)
                self.assertIn('path = "build.rs"', manifest)

    def test_package_scripts_run_the_rust_builders(self) -> None:
        package_data = json.loads(
            (self.project_root / "package.json").read_text(encoding="utf-8")
        )

        self.assertEqual(
            package_data["scripts"]["build:example-plugin-wasm"],
            "cargo run --quiet --manifest-path src/example_plugin/Cargo.toml "
            "--bin build-wasm",
        )
        self.assertEqual(
            package_data["scripts"]["build:rust-example-plugin-wasm"],
            "cargo run --quiet --manifest-path src/rust_example_plugin/Cargo.toml "
            "--bin build-wasm",
        )

    def test_documentation_uses_the_rust_builders(self) -> None:
        readme = (self.project_root / "README.md").read_text(encoding="utf-8")

        self.assertNotIn("build_wasm.mjs", readme)
        self.assertIn(
            "cargo run --manifest-path src/example_plugin/Cargo.toml --bin build-wasm",
            readme,
        )
        self.assertIn(
            "cargo run --manifest-path src/rust_example_plugin/Cargo.toml "
            "--bin build-wasm",
            readme,
        )
