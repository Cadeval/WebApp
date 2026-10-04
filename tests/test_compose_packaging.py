"""Deployment boundaries: private services, runtime secrets and startup gates."""
import json
from pathlib import Path
import re

from django.test import SimpleTestCase
import yaml

ROOT = Path(__file__).resolve().parents[1]


class ComposePackagingTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())

    def test_only_frontend_joins_external_network_and_no_ports_are_published(self):
        networks = self.compose["networks"]
        self.assertTrue(networks["swagnet"]["external"])
        self.assertEqual(networks["swagnet"]["name"], "${CADEVIL_SWAG_NETWORK:-swagnet}")
        self.assertEqual(networks["private"]["internal"], "${CADEVIL_PRIVATE_INTERNAL:-true}")
        for name, service in self.compose["services"].items():
            with self.subTest(service=name):
                self.assertNotIn("ports", service)
                self.assertNotIn("network_mode", service)
                self.assertNotIn("privileged", service)
                joined = set(service["networks"])
                self.assertEqual(joined, {"swagnet", "private"} if name == "frontend" else {"private"})

    def test_runtime_services_wait_for_database_cache_and_successful_migrations(self):
        services = self.compose["services"]
        for name in ("app", "migrate"):
            self.assertEqual(services[name]["depends_on"]["postgres"]["condition"], "service_healthy")
            self.assertEqual(services[name]["depends_on"]["redis"]["condition"], "service_healthy")
        self.assertEqual(services["app"]["depends_on"]["migrate"]["condition"], "service_completed_successfully")
        self.assertEqual(services["frontend"]["depends_on"]["app"]["condition"], "service_healthy")
        self.assertEqual(services["migrate"]["restart"], "no")
        self.assertEqual(services["migrate"]["command"], ["python", "manage.py", "migrate", "--noinput"])

    def test_secrets_are_runtime_files_granted_only_to_required_services(self):
        grants = {
            "app": {"django_secret_key", "postgres_password", "redis_password"},
            "migrate": {"django_secret_key", "postgres_password", "redis_password"},
            "postgres": {"postgres_admin_password", "postgres_password"}, "redis": {"redis_password"}, "frontend": set(),
        }
        for name, service in self.compose["services"].items():
            self.assertEqual(set(service.get("secrets", [])), grants[name])
            environment = service.get("environment", {})
            for forbidden in ("SECRET_KEY", "POSTGRES_PASSWORD", "REDIS_PASSWORD", "REDISCLI_AUTH", "DATABASE_PASSWORD"):
                self.assertNotIn(forbidden, environment)
            self.assertNotIn("build", service)
        for secret in self.compose["secrets"].values():
            self.assertEqual(set(secret), {"file"})
            self.assertIn(":?", secret["file"])
        self.assertIn("REDISCLI_AUTH", self.compose["services"]["redis"]["healthcheck"]["test"][1])
        self.assertIn("/run/secrets/redis_password", self.compose["services"]["redis"]["healthcheck"]["test"][1])
        self.assertIn("sed 's/\\r$//'", self.compose["services"]["redis"]["healthcheck"]["test"][1])
        self.assertIn("sed 's/\\r$//'", (ROOT / "docker/redis/start.sh").read_text())
        for name in ("app", "migrate"):
            self.assertNotIn("postgres_admin_password", self.compose["services"][name]["secrets"])

    def test_postgres18_storage_and_application_state_are_separate(self):
        services = self.compose["services"]
        postgres = services["postgres"]
        self.assertEqual(postgres["environment"]["PGDATA"], "/var/lib/postgresql/18/docker")
        self.assertEqual(postgres["volumes"][0]["target"], "/var/lib/postgresql")
        self.assertEqual(postgres["environment"]["POSTGRES_HOST_AUTH_METHOD"], "scram-sha-256")
        self.assertEqual(postgres["environment"]["POSTGRES_USER"], "postgres")
        self.assertEqual(postgres["environment"]["POSTGRES_PASSWORD_FILE"], "/run/secrets/postgres_admin_password")
        self.assertIn("--host=127.0.0.1", postgres["healthcheck"]["test"][1])
        for setting in ("log_statement=none", "log_min_error_statement=panic", "log_error_verbosity=terse", "log_parameter_max_length_on_error=0"):
            self.assertIn(setting, postgres["command"])
        for name in ("app", "migrate"):
            self.assertEqual(services[name]["volumes"][0]["source"], "app_data")
            self.assertEqual(services[name]["volumes"][0]["target"], "/app/data")
        self.assertEqual(set(self.compose["volumes"]), {"app_data", "postgres_data", "redis_data"})

    def test_fixed_official_images_and_readonly_exact_binds(self):
        services = self.compose["services"]
        for name, service in services.items():
            self.assertTrue(service["read_only"])
            self.assertEqual(service["cap_drop"], ["ALL"])
            self.assertIn("no-new-privileges:true", service["security_opt"])
            if name in {"frontend", "postgres", "redis"}:
                self.assertRegex(service["image"], r"^(nginx|postgres|redis):\d+\.[\w.\-]+@sha256:[a-f0-9]{64}$")
            for volume in service.get("volumes", []):
                if volume["type"] == "bind":
                    self.assertTrue(volume["read_only"])
                    self.assertFalse(volume["bind"]["create_host_path"])
                    self.assertTrue((ROOT / volume["source"]).is_file())
                    self.assertNotIn("docker.sock", volume["source"])
        self.assertEqual(services["frontend"]["user"], "101:101")

    def test_proxy_replaces_forwarded_headers_and_handles_long_websockets(self):
        main = (ROOT / "docker/frontend/nginx.conf").read_text()
        site = (ROOT / "docker/frontend/default.conf").read_text()
        self.assertIn("resolver 127.0.0.11", main)
        self.assertIn("server app:8000 resolve;", main)
        self.assertIn("zone cadevil_backend", main)
        self.assertIn("proxy_set_header Host $http_host;", site)
        self.assertIn("proxy_set_header Upgrade $http_upgrade;", site)
        self.assertIn("proxy_set_header Connection $connection_upgrade;", site)
        self.assertIn("proxy_set_header X-Forwarded-Proto https;", site)
        self.assertNotIn("X-Forwarded-Proto $scheme", site)
        self.assertNotIn("$proxy_add_x_forwarded_for", site)
        for directive in ("proxy_read_timeout", "proxy_send_timeout"):
            self.assertGreaterEqual(int(re.search(directive + r"\s+(\d+)s;", site)[1]), 900)
        log_format = re.search(r"log_format cadevil ([^;]+);", main)[1]
        self.assertNotIn("$request_uri", log_format)
        self.assertNotIn("$args", log_format)
        self.assertNotIn("$http_authorization", log_format)

    def test_deployment_files_and_secret_mounts_are_not_image_inputs(self):
        manifest = json.loads((ROOT / "docker/image-files.json").read_text())
        included = set(manifest["runtime"] + manifest["build_only"])
        for excluded in ("docker-compose.yml", "docker/compose.env.example", "docker/frontend/nginx.conf", "docker/frontend/default.conf", "docker/redis/start.sh", "docker/postgres/init-app.sh"):
            self.assertNotIn(excluded, included)
        self.assertFalse(any("/run/secrets/" in value or "secrets/" in value for value in included))

    def test_bootstrap_creates_limited_application_role_without_password_arguments(self):
        script = (ROOT / "docker/postgres/init-app.sh").read_text()
        self.assertIn("pg_read_file('/run/secrets/postgres_password')", script)
        self.assertIn("NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS", script)
        self.assertIn("CREATE DATABASE %I OWNER %I", script)
        self.assertIn("\\gexec", script)
        self.assertNotIn("--password", script)
        self.assertNotIn("$(cat", script)
        self.assertNotIn("PGPASSWORD", script)
        self.assertTrue((ROOT / "docker/postgres/init-app.sh").stat().st_mode & 0o111)
