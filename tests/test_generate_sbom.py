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
    from scripts.generate_sbom import Inventory, ROOT, npm_inventory, python_lock_coverage, validate_graph, browser_pki_inventory
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

    def test_runtime_npm_selection_follows_parser_closure_and_excludes_bundler(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "sbom") as directory:
            path=Path(directory)/"package-lock.json"
            path.write_text(json.dumps({"packages":{
                "":{"dependencies":{"parser":"1","bundler":"2"}},
                "node_modules/parser":{"version":"1","dependencies":{"asn1":"3"}},
                "node_modules/asn1":{"version":"3"},
                "node_modules/bundler":{"version":"2","optionalDependencies":{"platform":"4"}},
                "node_modules/platform":{"version":"4"},
            }}))
            runtime=Inventory("test","1","fixture")
            npm_inventory(runtime,path,False,root_packages={"parser"})
            self.assertEqual(set(runtime.components),{"pkg:npm/parser@1","pkg:npm/asn1@3"})
            self.assertEqual(runtime.dependencies["pkg:npm/parser@1"],{"pkg:npm/asn1@3"})
            development=Inventory("test-development","1","fixture")
            npm_inventory(development,path,True)
            self.assertEqual(set(development.components),{"pkg:npm/parser@1","pkg:npm/asn1@3","pkg:npm/bundler@2","pkg:npm/platform@4"})
            with self.assertRaisesRegex(ValueError,"runtime roots"):
                npm_inventory(Inventory("test","1","fixture"),path,False,root_packages={"unknown"})

    def test_browser_parser_inventory_has_exact_runtime_libraries_and_verified_license_bytes(self):
        runtime=Inventory("test","1","fixture")
        browser_pki_inventory(runtime,development=False)
        names={value["name"] for value in runtime.components.values()}
        self.assertEqual({value["name"] for value in runtime.components.values() if value["type"]=="library"},
                         {"pkijs","asn1js","pvtsutils","pvutils","bytestreamjs","tslib","@noble/hashes","es-module-lexer"})
        self.assertNotIn("esbuild",names)
        self.assertEqual(len(runtime.components),13)
        value=runtime.output()
        validate_graph(value)


if __name__ == "__main__":
    unittest.main()
