"""Policy canaries prevent host state from entering the reviewed Docker context."""
import importlib.util
import json
from pathlib import Path
import runpy
import tarfile
import tempfile
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase, override_settings
from django_bolt.testing import TestClient

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("container_context_policy", ROOT / "docker/context.py")
context = importlib.util.module_from_spec(spec)
spec.loader.exec_module(context)


class DockerContextPolicyTests(SimpleTestCase):
    def fixture(self, root):
        manifest = {"version": 1, "runtime": ["shared/public.py"], "build_only": ["Dockerfile", ".dockerignore", "docker/image-files.json"]}
        for path in manifest["runtime"] + manifest["build_only"]:
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("public application source\n")
        (root / "docker/image-files.json").write_text(json.dumps(manifest))
        (root / ".dockerignore").write_text(context.dockerignore(manifest))
        return manifest

    def test_current_checkout_has_an_exact_allowlist(self):
        manifest = context.validate()
        self.assertNotIn("config/settings/dev.py", manifest["runtime"])
        self.assertNotIn("plugin_manager/debug_processes.py", manifest["runtime"])
        self.assertIn("plugins/bim_model_manager/static/bim-demo/house-a.glb", manifest["runtime"])
        self.assertIn("plugins/example_plugin/static/css/ifc_editor.css", manifest["runtime"])
        self.assertIn("plugin_manager/resource_registry.py", manifest["runtime"])
        self.assertIn("plugin_manager/django_resources.py", manifest["runtime"])
        self.assertNotIn("plugins/resources.py", manifest["runtime"])
        self.assertIn("shared/migrations/0001_initial.py", manifest["runtime"])
        self.assertIn("sbom/cadevil.cdx.json", manifest["runtime"])
        for value in ("plugin_manager/certificate_authority.py", "plugin_manager/management/commands/plugin_ca.py",
                      "plugin_manager/migrations/0010_teams_certificate_authority.py", "plugin_manager/templates/plugin_manager/teams.html",
                      "resources/static/js/vendor/browser_pki.js", "resources/static/js/verified_plugin_worker.js"):
            self.assertIn(value,manifest["runtime"])
        self.assertNotIn("plugin_manager/templates/plugin_manager/_plugin_row.jinja2",manifest["runtime"])
        for value in ("scripts/vendor_browser_pki.mjs","scripts/browser-pki/package.json","scripts/browser-pki/package-lock.json"):
            self.assertIn(value,manifest["build_only"])
            self.assertNotIn(value,manifest["runtime"])

    def test_archive_ignores_secret_state_and_new_files_at_any_depth(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as output:
            root = Path(source)
            manifest = self.fixture(root)
            for path in (".env", ".git/config", "shared/credentials.py", "shared/nested/new.py", "resources/static/secret.txt", "plugins/bim_model_manager/static/bim-demo/private.ifc", "plugins/example_plugin/static/js/private.js", "data/user_uploads/private.ifc", "data/plugin-ca/private.root.pem", "plugin_manager/private-team-keys.json", "resources/static/js/vendor/private.js"):
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("PRIVATE_CANARY_DO_NOT_PACKAGE")
            bundle = Path(output) / "context.tar"
            context.archive(bundle, root)
            with tarfile.open(bundle) as archive:
                self.assertEqual(set(archive.getnames()), set(manifest["runtime"] + manifest["build_only"]))
                for member in archive.getmembers():
                    self.assertEqual(member.mode, 0o644)
                    self.assertEqual(member.uid, 0)
                    self.assertEqual(member.mtime, 0)
                    self.assertNotIn(b"PRIVATE_CANARY_DO_NOT_PACKAGE", archive.extractfile(member).read())

    def test_symlink_files_and_ancestors_are_rejected(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as outside:
            root = Path(source)
            self.fixture(root)
            target = root / "shared/public.py"
            target.unlink()
            private = Path(outside) / "private.py"
            private.write_text("private source")
            target.symlink_to(private)
            with self.assertRaisesRegex(ValueError, "Symlinks"):
                context.validate(root)
            target.unlink()
            (root / "shared").rmdir()
            (root / "shared").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "Symlinks"):
                context.validate(root)

    def test_manifest_refuses_wildcards_traversal_credentials_tests_and_private_models(self):
        with tempfile.TemporaryDirectory() as source:
            root = Path(source)
            manifest = self.fixture(root)
            for value in ("shared/**/*.py", "../private.py", "shared//public.py", "shared/.env", "shared/tests.py", "data/db.sqlite3", "models/private.ifc", "shared/key.pem", "plugin_manager/mcp_bridge.py"):
                with self.subTest(value=value):
                    manifest["runtime"] = [value]
                    (root / "docker/image-files.json").write_text(json.dumps(manifest))
                    with self.assertRaises(ValueError):
                        context.read_manifest(root)

    def test_dockerignore_must_match_reviewed_manifest(self):
        with tempfile.TemporaryDirectory() as source:
            root = Path(source)
            self.fixture(root)
            with (root / ".dockerignore").open("a") as handle:
                handle.write("!shared/credentials.py\n")
            with self.assertRaisesRegex(ValueError, "differs"):
                context.validate(root)

    def test_only_declared_rust_compiler_inputs_are_allowed_as_build_only(self):
        with tempfile.TemporaryDirectory() as source:
            root = Path(source)
            manifest = self.fixture(root)
            allowed = sorted(
                f"plugins/{plugin}/{filename}"
                for plugin in ("example_plugin", "rust_example_plugin")
                for filename in ("Cargo.toml", "Cargo.lock", "build.rs", "src/lib.rs")
            )
            self.assertEqual(context.RUST_BUILD_INPUTS, set(allowed))
            for value in allowed:
                target = root / value
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("public compiler input\n")
            manifest["build_only"].extend(allowed)
            (root / "docker/image-files.json").write_text(json.dumps(manifest))
            (root / ".dockerignore").write_text(context.dockerignore(manifest))
            self.assertEqual(context.validate(root)["build_only"], manifest["build_only"])

            # The narrow src exception must not permit Rust in the final
            # runtime or reopen arbitrary sources and test modules.
            for group, value in (
                ("runtime", "plugins/example_plugin/src/lib.rs"),
                ("build_only", "plugins/example_plugin/src/private.rs"),
                ("build_only", "plugins/unreviewed/src/lib.rs"),
                ("build_only", "plugins/example_plugin/src/test_signing.rs"),
                ("build_only", "tests/rust/example_plugin/lib.rs"),
            ):
                with self.subTest(group=group, value=value):
                    changed = self.fixture(root)
                    changed[group].append(value)
                    (root / "docker/image-files.json").write_text(json.dumps(changed))
                    with self.assertRaises(ValueError):
                        context.read_manifest(root)

    def test_received_build_context_rejects_unlisted_files(self):
        with tempfile.TemporaryDirectory() as source:
            root = Path(source)
            self.fixture(root)
            (root / "Dockerfile").unlink()
            (root / ".dockerignore").unlink()
            context.validate(root, received=True)
            (root / "shared/private.py").write_text("private")
            with self.assertRaisesRegex(ValueError, "does not match"):
                context.validate(root, received=True)


class ContainerSettingsTests(SimpleTestCase):
    def test_container_loads_runtime_secret_file_and_disables_mcp_plugins(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "secret"
            secret.write_text("container-test-only-key0123456789abcdefghij" * 2 + "\n")
            with patch.dict("os.environ", {"SECRET_KEY_FILE": str(secret), "ALLOWED_HOSTS": "example.test", "CADEVIL_TRUST_PROXY_HTTPS": "true"}, clear=True):
                # prod is imported lazily after reading the secret; do not use
                # an already cached prod module from another settings test.
                import sys
                saved = sys.modules.pop("config.settings.prod", None)
                try:
                    loaded = runpy.run_module("config.settings.container")
                finally:
                    sys.modules.pop("config.settings.prod", None)
                    if saved is not None:
                        sys.modules["config.settings.prod"] = saved
            self.assertFalse(loaded["DEBUG"])
            self.assertFalse(loaded["DEVELOPMENT_MCP_ENABLED"])
            self.assertEqual(loaded["PLUGIN_CA_DIRECTORY"], Path(loaded["BASE_DIR"]) / "data/plugin-ca")
            self.assertEqual(loaded["PLUGIN_CA_PUBLIC_URL"], "https://cadevil.org")
            self.assertFalse(any(plugin.startswith("cadevil.mcp.") for plugin in loaded["PLUGIN_BUILTINS"]))
            self.assertEqual(loaded["SECURE_PROXY_SSL_HEADER"], ("HTTP_X_FORWARDED_PROTO", "https"))
            self.assertEqual(loaded["SECURE_REDIRECT_EXEMPT"], [r"^healthz$"])

    def test_conflicting_or_unreadable_secrets_fail_without_secret_values(self):
        with patch.dict("os.environ", {"SECRET_KEY": "PRIVATE_SECRET_CANARY", "SECRET_KEY_FILE": "/unavailable/secret"}, clear=True):
            with self.assertRaises(ImproperlyConfigured) as raised:
                runpy.run_module("config.settings.container")
            self.assertNotIn("PRIVATE_SECRET_CANARY", str(raised.exception))
        with patch.dict("os.environ", {"SECRET_KEY_FILE": "/unavailable/secret"}, clear=True):
            with self.assertRaisesRegex(ImproperlyConfigured, "Cannot read"):
                runpy.run_module("config.settings.container")


@override_settings(ALLOWED_HOSTS=["testserver.local"])
class ContainerHealthTests(TestCase):
    def test_probe_requires_database_connection_without_returning_details(self):
        from shared.container_health import api
        response = TestClient(api).get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready"})
        self.assertEqual(response.headers["cache-control"], "no-store")
        with patch("shared.container_health.connection") as failed_database:
            failed_database.cursor.side_effect = DatabaseError("PRIVATE_DATABASE_PASSWORD")
            response = TestClient(api).get("/healthz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
        self.assertNotIn("PRIVATE_DATABASE_PASSWORD", response.content.decode())
