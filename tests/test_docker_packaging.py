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
        manifest = {"version": 1, "runtime": ["apps/public.py"], "build_only": ["Dockerfile", ".dockerignore", "docker/image-files.json"]}
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
        self.assertNotIn("apps/plugin_manager/debug_processes.py", manifest["runtime"])
        self.assertIn("resources/static/bim-demo/house-a.glb", manifest["runtime"])
        self.assertIn("apps/shared/migrations/0001_initial.py", manifest["runtime"])
        self.assertIn("sbom/cadevil.cdx.json", manifest["runtime"])

    def test_archive_ignores_secret_state_and_new_files_at_any_depth(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as output:
            root = Path(source)
            manifest = self.fixture(root)
            for path in (".env", ".git/config", "apps/credentials.py", "apps/nested/new.py", "resources/static/secret.txt", "data/user_uploads/private.ifc"):
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
            target = root / "apps/public.py"
            target.unlink()
            private = Path(outside) / "private.py"
            private.write_text("private source")
            target.symlink_to(private)
            with self.assertRaisesRegex(ValueError, "Symlinks"):
                context.validate(root)
            target.unlink()
            (root / "apps").rmdir()
            (root / "apps").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "Symlinks"):
                context.validate(root)

    def test_manifest_refuses_wildcards_traversal_credentials_tests_and_private_models(self):
        with tempfile.TemporaryDirectory() as source:
            root = Path(source)
            manifest = self.fixture(root)
            for value in ("apps/**/*.py", "../private.py", "apps//public.py", "apps/.env", "apps/tests.py", "data/db.sqlite3", "models/private.ifc", "apps/key.pem", "apps/plugin_manager/mcp_bridge.py"):
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
                handle.write("!apps/credentials.py\n")
            with self.assertRaisesRegex(ValueError, "differs"):
                context.validate(root)

    def test_received_build_context_rejects_unlisted_files(self):
        with tempfile.TemporaryDirectory() as source:
            root = Path(source)
            self.fixture(root)
            (root / "Dockerfile").unlink()
            (root / ".dockerignore").unlink()
            context.validate(root, received=True)
            (root / "apps/private.py").write_text("private")
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
        from apps.shared.container_health import api
        response = TestClient(api).get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready"})
        self.assertEqual(response.headers["cache-control"], "no-store")
        with patch("apps.shared.container_health.connection") as failed_database:
            failed_database.cursor.side_effect = DatabaseError("PRIVATE_DATABASE_PASSWORD")
            response = TestClient(api).get("/healthz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
        self.assertNotIn("PRIVATE_DATABASE_PASSWORD", response.content.decode())
