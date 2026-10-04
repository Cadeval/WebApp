"""Finite worker artifacts and production trust routes preserve workflow gates."""
import base64
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cryptography import x509
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings

from plugin_manager import browser_artifacts as artifacts
from plugin_manager.certificate_authority import certificate
from plugin_manager.models import PluginRecord, UserPluginSelection
from plugin_manager.signatures import canonical_payload
from tests.plugin_manager.package_fixtures import ensure_test_ca
from tests.plugin_manager import tests as native_tests


@override_settings(STATIC_URL='/static/')
class BundledArtifactBoundaryTests(SimpleTestCase):
    def test_declared_bundle_is_finite_and_requests_cannot_select_paths(self):
        self.assertEqual(set(artifacts.BUNDLED), {'cadevil.example.editor', 'cadevil.rust-example.editor',
                                                 'cadevil.browser.wasm-wrapper'})
        for files in artifacts.BUNDLED.values():
            for relative, asset in files.values():
                self.assertFalse(Path(relative).is_absolute())
                self.assertNotIn('..', Path(relative).parts)
                self.assertFalse(Path(asset).is_absolute())
                self.assertNotIn('..', Path(asset).parts)
        with patch.object(Path, 'read_bytes') as read:
            for identity in ('unknown', '../worker.js', '/etc/passwd', 'cadevil.example.editor/../../private'):
                with self.subTest(identity=identity), self.assertRaises(ValidationError):
                    artifacts.bundled_plugin_trust(identity)
            read.assert_not_called()

    def fixture(self, folder, content=b'fixed-worker'):
        root = Path(folder) / 'source'
        worker = root / 'owned/worker.js'
        worker.parent.mkdir(parents=True)
        worker.write_bytes(content)
        return root, worker, {'fixture': {'worker.js': ('owned/worker.js', 'js/worker.js')}}

    def test_only_exact_owned_bytes_and_declared_static_url_reach_signing(self):
        with TemporaryDirectory() as folder:
            root, worker, declaration = self.fixture(folder)
            with patch.object(artifacts, 'ROOT', root), patch.object(artifacts, 'BUNDLED', declaration), \
                    patch('plugin_manager.certificate_authority.bundled_trust', return_value={'signed': True}) as signer:
                result = artifacts.bundled_plugin_trust('fixture')
            signer.assert_called_once_with('fixture', {'worker.js': b'fixed-worker'})
            self.assertEqual(result['urls'], {'worker.js': '/static/js/worker.js'})
            self.assertEqual(result['entrypoint'], 'worker.js')
            self.assertEqual(result['wasm'], '')

    def test_symlink_file_and_ancestor_links_are_rejected_before_signing(self):
        for ancestor in (False, True):
            with self.subTest(ancestor=ancestor), TemporaryDirectory() as folder:
                root, worker, declaration = self.fixture(folder)
                outside = Path(folder) / 'outside'
                outside.mkdir()
                (outside / 'worker.js').write_bytes(b'outside')
                worker.unlink()
                if ancestor:
                    worker.parent.rmdir()
                    (root / 'owned').symlink_to(outside, target_is_directory=True)
                else:
                    worker.symlink_to(outside / 'worker.js')
                with patch.object(artifacts, 'ROOT', root), patch.object(artifacts, 'BUNDLED', declaration), \
                        patch('plugin_manager.certificate_authority.bundled_trust') as signer:
                    with self.assertRaises(ValidationError):
                        artifacts.bundled_plugin_trust('fixture')
                    signer.assert_not_called()

    def test_missing_empty_directory_and_oversized_artifacts_are_rejected(self):
        for variant in ('missing', 'empty', 'directory', 'oversized'):
            with self.subTest(variant=variant), TemporaryDirectory() as folder:
                root, worker, declaration = self.fixture(folder)
                worker.unlink()
                if variant == 'directory':
                    worker.mkdir()
                elif variant != 'missing':
                    worker.write_bytes(b'' if variant == 'empty' else b'x' * 17)
                with patch.object(artifacts, 'ROOT', root), patch.object(artifacts, 'BUNDLED', declaration), \
                        patch.object(artifacts, 'MAX_ARTIFACT_BYTES', 16), \
                        patch('plugin_manager.certificate_authority.bundled_trust') as signer:
                    with self.assertRaises(ValidationError):
                        artifacts.bundled_plugin_trust('fixture')
                    signer.assert_not_called()

    def test_size_is_checked_again_after_reading(self):
        with TemporaryDirectory() as folder:
            root, worker, declaration = self.fixture(folder, b'small')
            with patch.object(artifacts, 'ROOT', root), patch.object(artifacts, 'BUNDLED', declaration), \
                    patch.object(artifacts, 'MAX_ARTIFACT_BYTES', 16), \
                    patch.object(Path, 'read_bytes', return_value=b'x' * 17), \
                    patch('plugin_manager.certificate_authority.bundled_trust') as signer:
                with self.assertRaises(ValidationError):
                    artifacts.bundled_plugin_trust('fixture')
                signer.assert_not_called()


@override_settings(DEBUG=False, STATIC_URL='/static/')
class BrowserArtifactRouteTests(TestCase):
    def setUp(self):
        native_tests.NativePluginTests.setUp(self)
        self.ca_settings = override_settings(PLUGIN_CA_DIRECTORY=Path(self.folder.name) / 'ca')
        self.ca_settings.enable()
        self.addCleanup(self.ca_settings.disable)
        self.authority = ensure_test_ca()
        self.plugin, _ = PluginRecord.objects.update_or_create(plugin_id='cadevil.example.editor', defaults={
            'name': 'IFC editor', 'version': '2.0.4', 'source': PluginRecord.Source.PACKAGE,
            'enabled': True, 'compatibility': PluginRecord.Compatibility.BOTH, 'error': ''})
        self.client.logout()
        self.client.force_login(self.regular)

    def select(self):
        UserPluginSelection.objects.get_or_create(user=self.regular, plugin=self.plugin)

    def test_bootstrap_requires_login_and_an_available_selected_workflow(self):
        self.client.logout()
        response = self.client.get('/plugins/worker-bootstrap.js')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/mycelium/login', response['Location'])
        self.client.force_login(self.regular)
        self.assertEqual(self.client.get('/plugins/worker-bootstrap.js').status_code, 404)
        self.select()
        self.plugin.enabled = False
        self.plugin.save(update_fields=['enabled'])
        self.assertEqual(self.client.get('/plugins/worker-bootstrap.js').status_code, 404)

    def test_bootstrap_is_javascript_with_restrictive_worker_csp_and_no_store(self):
        self.select()
        response = self.client.get('/plugins/worker-bootstrap.js')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Content-Type'], 'application/javascript')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response['Cross-Origin-Resource-Policy'], 'same-origin')
        self.assertEqual(response['Content-Security-Policy'],
            "default-src 'none'; script-src blob: 'wasm-unsafe-eval'; connect-src 'none'; worker-src 'none'; base-uri 'none'; form-action 'none'")
        self.assertEqual(response.content, (artifacts.ROOT / 'resources/static/js/plugin_worker_bootstrap.js').read_bytes())

    def test_trust_route_requires_exact_selected_available_plugin_scope(self):
        self.client.logout()
        self.assertEqual(self.client.get('/plugins/cadevil.example.editor/trust.json').status_code, 302)
        self.client.force_login(self.regular)
        self.assertEqual(self.client.get('/plugins/cadevil.example.editor/trust.json').status_code, 404)
        self.select()
        self.assertEqual(self.client.get('/plugins/cadevil.rust-example.editor/trust.json').status_code, 404)
        self.assertEqual(self.client.get('/plugins/cadevil.browser.wasm-wrapper/trust.json').status_code, 404)
        self.plugin.enabled = False
        self.plugin.save(update_fields=['enabled'])
        self.assertEqual(self.client.get('/plugins/cadevil.example.editor/trust.json').status_code, 404)

    def test_production_trust_signs_the_exact_finite_worker_and_wasm_bytes(self):
        self.select()
        response = self.client.get('/plugins/cadevil.example.editor/trust.json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        data = response.json()
        self.assertEqual(data['plugin_id'], self.plugin.plugin_id)
        self.assertEqual(data['entrypoint'], 'worker.js')
        self.assertEqual(data['wasm'], 'module.wasm')
        declared = artifacts.BUNDLED[self.plugin.plugin_id]
        expected = {name: hashlib.sha256((artifacts.ROOT / relative).read_bytes()).hexdigest()
                    for name, (relative, _) in declared.items()}
        self.assertEqual(data['files'], expected)
        self.assertEqual(data['urls'], {name: '/static/' + asset for name, (_, asset) in declared.items()})
        leaf, issuer, root = map(certificate, data['certificate_chain'])
        root.verify_directly_issued_by(root)
        issuer.verify_directly_issued_by(root)
        leaf.verify_directly_issued_by(issuer)
        self.assertEqual(leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
                         .get_values_for_type(x509.UniformResourceIdentifier), ['urn:cadevil:publisher:server'])
        leaf.public_key().verify(base64.b64decode(data['signature']['signature'], validate=True), canonical_payload(expected))
        self.assertNotIn(b'PRIVATE KEY', response.content)
        self.assertNotIn(b'encrypted_private_key', response.content)

    def test_missing_ca_returns_no_store_unavailable_response_without_worker_bytes(self):
        self.select()
        self.authority.active = False
        self.authority.save(update_fields=['active'])
        response = self.client.get('/plugins/cadevil.example.editor/trust.json')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(set(response.json()), {'error'})
