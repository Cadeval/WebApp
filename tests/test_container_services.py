"""Regression checks for mounted database/cache secrets and URL handling."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[1]
KEY = "compose-regression-key-0123456789-abcdefghijklmnopqrstuvwxyz-ABCDEFG"
PASSWORD = "test-only:@/%+\" with spaces"


class ContainerServiceSettingsTests(SimpleTestCase):
    def settings_process(self, variables, checks):
        environment = {key: value for key, value in os.environ.items() if key not in {
            "SECRET_KEY", "SECRET_KEY_FILE", "DATABASE_URL", "DATABASE_PASSWORD_FILE",
            "REDIS_URL", "REDIS_PASSWORD_FILE", "DJANGO_SETTINGS_MODULE",
        }}
        environment.update({"SECRET_KEY": KEY, "ALLOWED_HOSTS": "example.test", **variables})
        program = "import os,json;from config.settings import container as c;" + checks
        return subprocess.run([sys.executable, "-c", program], cwd=ROOT, env=environment,
                              capture_output=True, text=True, timeout=15)

    def secret(self, directory, name, value):
        path = Path(directory)/name
        path.write_text(value, encoding="utf-8")
        return str(path)

    def test_file_credentials_are_decoded_without_mutating_inherited_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            key = self.secret(directory, "key", KEY + "\n")
            database = self.secret(directory, "database", PASSWORD + "\n")
            redis = self.secret(directory, "redis", PASSWORD + "\n")
            variables = {"SECRET_KEY": "", "SECRET_KEY_FILE": key,
                         "DATABASE_URL": "postgresql://cadevil@postgres:5432/cadevil",
                         "DATABASE_PASSWORD_FILE": database, "REDIS_URL": "redis://redis:6379/0",
                         "REDIS_PASSWORD_FILE": redis}
            # An absent environment SECRET_KEY is required when using a file.
            result = self.settings_process(variables,
                "from urllib.parse import urlsplit,unquote;"
                f"assert c.DATABASES['default']['PASSWORD']=={PASSWORD!r};"
                f"assert unquote(urlsplit(c.CACHES['default']['LOCATION']).password)=={PASSWORD!r};"
                "assert 'SECRET_KEY' not in os.environ;"
                "assert os.environ['REDIS_URL']=='redis://redis:6379/0';"
                "assert c.SESSION_ENGINE=='django.contrib.sessions.backends.cached_db';"
                "assert c.DATABASES['default']['OPTIONS']['connect_timeout']==5;"
                "assert c.CACHES['default']['OPTIONS']['socket_timeout']==2;"
                "assert c.BOLT_MAX_UPLOAD_SIZE==64*1024*1024;print('passed')")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "passed")

    def test_percent_encoded_database_url_fields_are_decoded(self):
        result = self.settings_process({"DATABASE_URL": "postgresql://user%40team:pass%3A%40%2F%25@postgres/db%20name"},
            "assert c.DATABASES['default']['USER']=='user@team';"
            "assert c.DATABASES['default']['PASSWORD']=='pass:@/%';"
            "assert c.DATABASES['default']['NAME']=='db name';print('passed')")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_database_file_requires_postgres_url_without_embedded_password(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.secret(directory, "database", PASSWORD)
            for url in ("sqlite:///test.sqlite3", "postgresql://user:embedded-canary@postgres/db",
                        "postgresql://user:@postgres/db"):
                with self.subTest(url=url):
                    result = self.settings_process({"DATABASE_URL": url, "DATABASE_PASSWORD_FILE": path}, "")
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("requires a PostgreSQL URL without a password", result.stderr)
                    self.assertNotIn(PASSWORD, result.stderr)
                    self.assertNotIn("embedded-canary", result.stderr)

    def test_redis_file_requires_valid_url_without_embedded_password(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.secret(directory, "redis", PASSWORD)
            for url in ("", "http://redis:6379/0", "redis://", "redis://redis:bad/0",
                        "redis://:embedded-canary@redis/0", "redis://:@redis/0"):
                with self.subTest(url=url):
                    result = self.settings_process({"REDIS_URL": url, "REDIS_PASSWORD_FILE": path}, "")
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("requires a Redis URL without a password", result.stderr)
                    self.assertNotIn(PASSWORD, result.stderr)
                    self.assertNotIn("embedded-canary", result.stderr)

    def test_invalid_or_oversized_mounted_secret_fails_without_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            for value in ("", "private-canary\nsecond-line", "x"*65537):
                with self.subTest(size=len(value)):
                    path = self.secret(directory, "database", value)
                    result = self.settings_process({"DATABASE_URL": "postgresql://user@postgres/db",
                                                    "DATABASE_PASSWORD_FILE": path}, "")
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("Cannot read a valid DATABASE_PASSWORD_FILE", result.stderr)
                    self.assertNotIn("private-canary", result.stderr)

    def test_unreadable_secret_fails_without_path_or_values(self):
        result = self.settings_process({"REDIS_URL": "redis://redis/0",
                                        "REDIS_PASSWORD_FILE": "/nonexistent/private-canary-file"}, "")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cannot read a valid REDIS_PASSWORD_FILE", result.stderr)
        self.assertNotIn("private-canary-file", result.stderr)

    def test_optional_services_leave_sqlite_and_database_sessions_available(self):
        result = self.settings_process({},
            "assert c.DATABASES['default']['ENGINE']=='django.db.backends.sqlite3';"
            "assert not hasattr(c,'SESSION_ENGINE');assert not c.DEVELOPMENT_MCP_ENABLED;print('passed')")
        self.assertEqual(result.returncode, 0, result.stderr)
