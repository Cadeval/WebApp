"""Checks for inventory boundaries that a syntactically valid BOM cannot prove."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

if __package__ in {None, ""}:
    # make sbom-check runs this file with the isolated SBOM tool interpreter.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SBOM_TOOL_MISSING = None
try:
    from scripts.generate_sbom import Inventory, ROOT, npm_inventory, python_lock_coverage, validate_graph
except ModuleNotFoundError as error:
    if error.name not in {"cyclonedx", "packageurl"}:
        raise
    SBOM_TOOL_MISSING = "Requires the isolated SBOM tool environment; run make sbom-check."


@unittest.skipIf(SBOM_TOOL_MISSING, SBOM_TOOL_MISSING)
class InventoryTests(unittest.TestCase):
    def test_locked_runtime_follows_requested_extras_and_all_platform_branches(self):
        lock = {"package": [
            {"name": "app", "version": "1", "source": {"virtual": "."}, "dependencies": [{"name": "framework", "extra": ["mcp"]}], "dev-dependencies": {"dev": [{"name": "checker"}]}},
            {"name": "framework", "version": "2", "dependencies": [{"name": "windows", "marker": "sys_platform == 'win32'"}], "optional-dependencies": {"mcp": [{"name": "mcp"}], "unused": [{"name": "never"}]}},
            {"name": "mcp", "version": "3", "dependencies": [{"name": "framework"}]},
            {"name": "windows", "version": "4"},
            {"name": "checker", "version": "5", "dependencies": [{"name": "framework"}]},
            {"name": "never", "version": "6"},
        ]}
        runtime = {("framework", "2"), ("mcp", "3"), ("windows", "4")}
        for kind in ("virtual", "editable"):
            with self.subTest(project_source=kind):
                lock["package"][0]["source"] = {kind: "."}
                self.assertEqual(python_lock_coverage(lock, False), runtime)
                self.assertEqual(python_lock_coverage(lock, True), runtime | {("checker", "5")})

    def test_graph_rejects_dangling_dependency(self):
        value = {"metadata": {"component": {"bom-ref": "root"}}, "components": [], "dependencies": [{"ref": "root", "dependsOn": ["missing"]}]}
        with self.assertRaisesRegex(ValueError, "Dangling"):
            validate_graph(value)

    def test_npm_nested_version_resolves_before_root_and_absent_optional_peer_is_allowed(self):
        # Place the disposable lock under the repository so evidence remains
        # relative; no actual application lock is changed.
        with tempfile.TemporaryDirectory(dir=ROOT / "sbom") as directory:
            path = Path(directory) / "package-lock.json"
            path.write_text(json.dumps({"packages": {
                "": {"dependencies": {"parent": "1", "shared": "2"}},
                "node_modules/parent": {"version": "1", "dependencies": {"shared": "1"}, "peerDependencies": {"optional": "1"}, "peerDependenciesMeta": {"optional": {"optional": True}}},
                "node_modules/shared": {"version": "2"},
                "node_modules/parent/node_modules/shared": {"version": "1"},
            }}))
            inventory = Inventory("test", "1", "fixture")
            npm_inventory(inventory, path, True)
            self.assertEqual(inventory.dependencies["pkg:npm/parent@1"], {"pkg:npm/shared@1"})
            self.assertEqual(len(inventory.components), 3)

    def test_npm_missing_required_dependency_fails(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "sbom") as directory:
            path = Path(directory) / "package-lock.json"
            path.write_text(json.dumps({"packages": {"": {"dependencies": {"missing": "1"}}}}))
            with self.assertRaisesRegex(ValueError, "Unresolved npm dependency"):
                npm_inventory(Inventory("test", "1", "fixture"), path, True)


if __name__ == "__main__":
    unittest.main()
