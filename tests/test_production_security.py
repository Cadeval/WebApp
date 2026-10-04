"""Production settings fail closed for credentials and development tools."""
import os
import runpy
from unittest.mock import patch
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase


class ProductionSecurityTests(SimpleTestCase):
    def load(self, key):
        with patch.dict(os.environ, {'SECRET_KEY': key, 'CSRF_TRUSTED_ORIGINS': 'https://example.test', 'DEVELOPMENT_MCP_ENABLED': 'true'}):
            return runpy.run_module('config.settings.prod')

    def test_development_or_weak_keys_are_rejected(self):
        for key in ('changeme', 'a' * 80, 'django-insecure-' + 'abcdefghijklmnop' * 5):
            with self.subTest(key=key[:16]), self.assertRaises(ImproperlyConfigured):
                self.load(key)

    def test_strong_key_keeps_cors_explicit_and_debug_disabled(self):
        settings = self.load('test-only-abcdefghijklm0123456789' * 3)
        self.assertEqual(settings['MEDIA_URL'], '/')
        self.assertFalse(settings['DEBUG'])
        self.assertFalse(settings['CORS_ALLOW_ALL_ORIGINS'])
        self.assertEqual(settings['CORS_ALLOWED_ORIGINS'], ['https://example.test'])
        self.assertTrue(settings['CORS_ALLOW_CREDENTIALS'])
        self.assertEqual(settings['CORS_PREFLIGHT_MAX_AGE'], 86400)
