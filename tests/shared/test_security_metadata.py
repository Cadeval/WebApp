"""Disclosure remains public, plain text and independent of private storage."""
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from django_bolt.testing import TestClient

from shared.security_metadata import api, disclosure_fields


class SecurityMetadataTests(SimpleTestCase):
    def setUp(self):
        self.client = TestClient(api, base_url='http://testserver')
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_public_rfc_9116_file_has_contact_expiry_utf8_and_nosniff(self):
        response = self.client.get('/.well-known/security.txt')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Type'], 'text/plain; charset=utf-8')
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertNotIn('<html', response.text)
        fields = dict(line.split(': ', 1) for line in response.text.splitlines())
        self.assertEqual(fields['Contact'], 'mailto:mia@meanderingmind.me')
        self.assertEqual(fields['Preferred-Languages'], 'en, de')
        expires = datetime.fromisoformat(fields['Expires'].replace('Z', '+00:00'))
        self.assertGreater(expires, datetime(2026, 10, 4, tzinfo=timezone.utc))
        self.assertLess(expires, datetime(2027, 10, 4, tzinfo=timezone.utc))
        self.assertTrue(response.text.endswith('\n'))

    def test_legacy_spellings_redirect_to_standard_location(self):
        for path in ('/.well_known/security.txt', '/security.txt'):
            response = self.client.get(path, follow_redirects=False)
            self.assertEqual(response.status_code, 301)
            self.assertEqual(response.headers['Location'], '/.well-known/security.txt')

    @override_settings(SECURITY_TXT_CONTACT='mailto:security@example.org',
                       SECURITY_TXT_EXPIRES='2027-02-01T00:00:00Z',
                       SECURITY_TXT_CANONICAL='https://example.org/.well-known/security.txt',
                       SECURITY_TXT_POLICY='https://example.org/security')
    def test_operator_metadata_is_used_without_guessing_or_echoing_request_host(self):
        response = self.client.get('/.well-known/security.txt')
        self.assertIn('Contact: mailto:security@example.org\n', response.text)
        self.assertIn('Expires: 2027-02-01T00:00:00Z\n', response.text)
        self.assertIn('Canonical: https://example.org/.well-known/security.txt\n', response.text)
        self.assertIn('Policy: https://example.org/security\n', response.text)
        self.assertNotIn('testserver', response.text)

    def test_invalid_configuration_cannot_inject_fields_or_unsafe_links(self):
        examples = {
            'SECURITY_TXT_CONTACT': ['mailto:person@example.org\nContact: https://evil.invalid',
                'javascript:alert(1)', 'https://user:secret@example.org/contact',
                'mailto:person@example.org%0d%0aExpires:never', 'mailto:no-email'],
            'SECURITY_TXT_CANONICAL': ['http://example.org/.well-known/security.txt', 'https:///bad'],
            'SECURITY_TXT_POLICY': ['javascript:alert(1)', 'https://example.org/#fragment'],
            'SECURITY_TXT_EXPIRES': ['never', '2027-01-04 00:00:00Z', '2027-01-04T00:00:00',
                '2027-02-31T00:00:00Z', '2027-01-04T00:00:00Z\nContact: mailto:other@example.org'],
        }
        for field, values in examples.items():
            for value in values:
                with self.subTest(field=field, value=value), override_settings(**{field: value}):
                    with self.assertRaises(ValueError):
                        disclosure_fields()
                    response = self.client.get('/.well-known/security.txt')
                    self.assertEqual(response.status_code, 503)
                    self.assertNotIn(value, response.text)

    def test_policy_and_accessibility_support_full_pages_and_htmx_content(self):
        for route, heading in (('/security', 'Security'), ('/accessibility', 'Accessibility')):
            full = self.client.get(route)
            self.assertEqual(full.status_code, 200)
            self.assertIn('<html', full.text)
            self.assertIn(heading, full.text)
            self.assertEqual(full.text.count('id="content-container"'), 1)
            fragment = self.client.get(route, headers={'HX-Request': 'true'})
            self.assertEqual(fragment.status_code, 200)
            self.assertNotIn('<html', fragment.text)
            self.assertIn(heading, fragment.text)
            self.assertEqual(fragment.text.count('id="content-container"'), 1)
            self.assertIn('HX-Request', fragment.headers['Vary'])

    def test_sbom_route_only_reads_fixed_release_artifact_and_uses_json_type(self):
        with TemporaryDirectory() as directory, patch('shared.security_metadata.PROJECT_ROOT', Path(directory)):
            root=Path(directory); (root/'sbom').mkdir()
            content={'bomFormat':'CycloneDX','specVersion':'1.6','version':1}
            (root/'sbom/cadevil.cdx.json').write_text(json.dumps(content))
            (root/'private.json').write_text('private model data')
            response = self.client.get('/security/sbom.json?path=private.json')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['Content-Type'], 'application/vnd.cyclonedx+json')
            self.assertEqual(response.json(), content)
            self.assertIn('cadevil.cdx.json', response.headers['Content-Disposition'])
            (root/'sbom/cadevil.cdx.json').unlink()
            self.assertEqual(self.client.get('/security/sbom.json').status_code, 503)
