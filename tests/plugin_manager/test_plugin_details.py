"""Catalog navigation, per-user access and observed-version evidence."""
from importlib import import_module
from unittest.mock import patch
from types import SimpleNamespace

from django.apps import apps
from django.db import connection, OperationalError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from plugin_manager.forms import PluginUploadForm
from plugin_manager.models import PluginRecord, UserPluginSelection
from plugin_manager.admin import PluginRecordAdmin
from plugin_manager.registry import DiscoveryResult, PluginRegistry
from plugin_manager.services import create_uploaded_plugin
from plugin_manager.store import plugin_detail_metadata
from tests.plugin_manager.package_fixtures import signed_package
from tests.plugin_manager import tests as native_tests


class PluginVersionHistoryTests(TestCase):
    def test_observed_history_is_read_only_in_django_administration(self):
        self.assertIn('version_history', PluginRecordAdmin.readonly_fields)

    def test_successful_discovery_records_observed_versions_once(self):
        registry = PluginRegistry()
        for version in ['1.0.0', '1.0.0', '2.0.0', '1.0.0']:
            registry.sync_plugin_records([DiscoveryResult(plugin_id='history.test', version=version, api_version='1.0')])
        record = PluginRecord.objects.get(plugin_id='history.test')
        self.assertEqual([entry['version'] for entry in record.version_history], ['1.0.0', '2.0.0'])
        self.assertTrue(all(entry['basis'] == 'verified' for entry in record.version_history))
        self.assertTrue(all(entry['observed_at'] for entry in record.version_history))
        registry.sync_plugin_records([DiscoveryResult(plugin_id='history.test', version='3.0.0', error='Failed validation')])
        record.refresh_from_db()
        self.assertEqual([entry['version'] for entry in record.version_history], ['1.0.0', '2.0.0'])
        self.assertFalse(record.enabled)

    def test_upload_records_real_signed_version_hash_and_time(self):
        content = signed_package(plugin_id='history.upload')
        form = PluginUploadForm({}, {'artifact': SimpleUploadedFile('plugin.zip', content)})
        self.assertTrue(form.is_valid(), form.errors)
        record = create_uploaded_plugin(form, form.cleaned_data['artifact'].signing_key.owner)
        self.assertEqual(len(record.version_history), 1)
        entry = record.version_history[0]
        self.assertEqual(entry['version'], record.version)
        self.assertEqual(entry['content_hash'], record.content_hash)
        self.assertEqual(entry['source'], 'upload')
        self.assertEqual(entry['basis'], 'verified')
        self.assertEqual(entry['observed_at'], record.uploaded_at.isoformat())

    def test_migration_backfill_preserves_current_metadata_without_fake_releases(self):
        record = PluginRecord.objects.create(plugin_id='history.before', version='2.0.0', api_version='1.0', enabled=False, error='existing error')
        empty = PluginRecord.objects.create(plugin_id='history.empty', version='')
        old_timestamp = record.discovered_at
        migration = import_module('plugin_manager.migrations.0009_plugin_version_history')
        migration.preserve_current_versions(apps, SimpleNamespace(connection=connection))
        record.refresh_from_db(); empty.refresh_from_db()
        self.assertEqual(record.version_history, [{'version': '2.0.0', 'api_version': '1.0', 'source': 'package',
                          'content_hash': '', 'observed_at': old_timestamp.isoformat(), 'basis': 'stored'}])
        self.assertEqual(empty.version_history, [])
        self.assertEqual(record.discovered_at, old_timestamp)
        self.assertEqual(record.error, 'existing error'); self.assertFalse(record.enabled)
        PluginRegistry().sync_plugin_records([DiscoveryResult(plugin_id='history.before', version='2.1.0', api_version='1.0')])
        record.refresh_from_db()
        self.assertEqual([entry['version'] for entry in record.version_history], ['2.0.0', '2.1.0'])
        self.assertEqual([entry['basis'] for entry in record.version_history], ['stored', 'verified'])

    def test_missing_history_column_skips_all_discovery_writes(self):
        record = PluginRecord.objects.create(plugin_id='history.before', version='2.0.0', enabled=True)
        with patch.object(PluginRecord.objects, 'values_list', side_effect=OperationalError('missing version_history')), \
             patch.object(PluginRecord.objects, 'filter') as filtered, self.assertLogs('plugin_manager.registry', 'WARNING'):
            PluginRegistry().sync_plugin_records([DiscoveryResult(plugin_id='history.before', version='3.0.0')])
        filtered.assert_not_called()
        record.refresh_from_db()
        self.assertEqual(record.version, '2.0.0'); self.assertTrue(record.enabled)

    def test_old_image_insert_without_new_column_uses_database_default(self):
        record = PluginRecord.objects.create(plugin_id='history.old-writer', version='1.0.0')
        table = PluginRecord._meta.db_table
        quote = connection.ops.quote_name
        columns = [field.column for field in PluginRecord._meta.local_fields if field.name not in {'id', 'version_history'}]
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT {", ".join(quote(col) for col in columns)} FROM {quote(table)} WHERE id = %s', [record.pk])
            values = list(cursor.fetchone()); values[columns.index('plugin_id')] = 'history.old-writer-new'
            cursor.execute(f'INSERT INTO {quote(table)} ({", ".join(quote(col) for col in columns)}) VALUES ({", ".join(["%s"] * len(columns))})', values)
        self.assertEqual(PluginRecord.objects.get(plugin_id='history.old-writer-new').version_history, [])


@override_settings(STATIC_URL='/static/', DEBUG=True)
class PluginDetailsBrowserTests(TestCase):
    def setUp(self):
        native_tests.NativePluginTests.setUp(self)
        self.available = PluginRecord.objects.create(plugin_id='detail.available', name='Available detail tool', version='1.2.0')
        self.available.observe_version(); self.available.save()
        self.disabled = PluginRecord.objects.create(plugin_id='detail.disabled', name='Disabled detail tool', enabled=False)

    def url(self, record=None):
        return f'/plugins/{(record or self.available).plugin_id}/details/'

    def test_three_tabs_use_htmx_and_keep_developer_upload_separate(self):
        response = self.client.get('/plugins/manage/', {'tab': 'developer'}, HTTP_HX_REQUEST='true')
        self.assertContains(response, 'id="content-container"', count=1)
        html = response.content.decode()
        for key, label in [('workflows', 'Workflows'), ('management', 'Plugin management'), ('developer', 'Developer')]:
            self.assertIn(f'hx-get="/plugins/manage/?tab={key}"', html)
            self.assertIn(f'>{label}</a>', html)
        self.assertIn('data-active-tab-id="plugin-catalog-tab-developer"', html)
        developer = html.split('id="plugin-catalog-panel-developer"', 1)[1].split('</div>\n    <dialog', 1)[0]
        self.assertIn('name="artifact"', developer)
        self.assertIn('Manage signing keys in User settings', developer)
        before_developer = html.split('id="plugin-catalog-panel-developer"', 1)[0]
        self.assertNotIn('name="artifact"', before_developer)
        self.assertNotIn('plugin-table', html)

    def test_full_fragment_and_drawer_have_correct_boundaries_and_history_fallback(self):
        full = self.client.get(self.url())
        self.assertContains(full, '<html'); self.assertContains(full, 'id="content-container"', count=1)
        fragment = self.client.get(self.url(), HTTP_HX_REQUEST='true')
        self.assertNotContains(fragment, '<html'); self.assertContains(fragment, 'id="content-container"', count=1)
        drawer = self.client.get(self.url(), HTTP_HX_REQUEST='true', HTTP_HX_TARGET='plugin-detail-body')
        self.assertNotContains(drawer, '<html'); self.assertNotContains(drawer, 'id="content-container"')
        self.assertContains(drawer, 'data-plugin-detail-panel'); self.assertContains(drawer, 'Version history')
        self.assertEqual(drawer['Cache-Control'], 'private, no-store')
        self.assertIn('HX-Target', drawer['Vary'])
        for headers in [{'HTTP_HX_HISTORY_RESTORE_REQUEST': 'true'}, {'HTTP_HX_REQUEST_TYPE': 'full'}]:
            history = self.client.get(self.url(), HTTP_HX_REQUEST='true', HTTP_HX_TARGET='plugin-detail-body', **headers)
            self.assertContains(history, '<html'); self.assertContains(history, 'id="content-container"', count=1)

    def test_htmx4_tag_and_id_target_selects_only_the_detail_panel(self):
        drawer = self.client.get(self.url(), HTTP_HX_REQUEST='true',
                                 HTTP_HX_REQUEST_TYPE='partial', HTTP_HX_TARGET='div#plugin-detail-body')
        self.assertContains(drawer, 'data-plugin-detail-panel')
        self.assertNotContains(drawer, 'id="content-container"')
        for target in ['div#other', 'div#plugin-detail-body extra', 'body', '']:
            with self.subTest(target=target):
                regular = self.client.get(self.url(), HTTP_HX_REQUEST='true', HTTP_HX_TARGET=target)
                self.assertContains(regular, 'id="content-container"', count=1)

    def test_regular_details_hide_global_controls_and_private_errors(self):
        self.client.force_login(self.regular)
        response = self.client.get(self.url(), HTTP_HX_REQUEST='true', HTTP_HX_TARGET='plugin-detail-body')
        self.assertNotContains(response, '/plugins/detail.available/enable/')
        self.assertNotContains(response, '/plugins/detail.available/disable/')
        self.assertContains(response, 'Add to workflow')
        self.assertEqual(self.client.get(self.url(self.disabled)).status_code, 404)
        UserPluginSelection.objects.create(user=self.regular, plugin=self.disabled)
        self.disabled.error = 'private internal stack'; self.disabled.save()
        saved = self.client.get(self.url(self.disabled))
        self.assertContains(saved, 'Remove from workflow'); self.assertNotContains(saved, 'private internal stack')
        self.assertEqual(self.client.post('/plugins/detail.available/disable/').status_code, 403)
        self.available.refresh_from_db(); self.assertTrue(self.available.enabled)

    def test_admin_controls_at_top_preserve_selections_and_archive(self):
        UserPluginSelection.objects.create(user=self.regular, plugin=self.available)
        detail = self.client.get(self.url(), HTTP_HX_REQUEST='true', HTTP_HX_TARGET='plugin-detail-body')
        html = detail.content.decode()
        self.assertLess(html.index('>Install</button>'), html.index('<dt>Uploader</dt>'))
        self.assertLess(html.index('>Uninstall</button>'), html.index('<dt>Uploader</dt>'))
        response = self.client.post('/plugins/detail.available/disable/', {'tab': 'management'}, HTTP_HX_REQUEST='true')
        self.assertContains(response, 'data-active-tab-id="plugin-catalog-tab-management"')
        self.available.refresh_from_db(); self.assertFalse(self.available.enabled)
        self.assertTrue(UserPluginSelection.objects.filter(user=self.regular, plugin=self.available).exists())
        self.assertEqual(len(self.available.version_history), 1)

    def test_signed_upload_has_exact_size_and_real_uploader(self):
        form = PluginUploadForm({}, {'artifact': SimpleUploadedFile('plugin.zip', signed_package(self.regular, 'detail.uploaded'))})
        self.assertTrue(form.is_valid(), form.errors)
        record = create_uploaded_plugin(form, self.regular)
        detail = self.client.get(self.url(record))
        self.assertContains(detail, f'({record.artifact.size} bytes, signed archive)')
        self.assertContains(detail, self.regular.get_username())
        self.assertContains(detail, 'Verified upload')
        self.assertTrue(record.artifact.storage.exists(record.artifact.name))
        self.client.post(f'/plugins/{record.plugin_id}/disable/')
        record.refresh_from_db()
        self.assertTrue(record.artifact.storage.exists(record.artifact.name))

    def test_installed_size_is_honest_and_missing_upload_does_not_guess(self):
        detail = self.client.get(self.url())
        self.assertContains(detail, 'Site installation')
        self.assertContains(detail, 'Unavailable for installed Python packages')
        record = PluginRecord(source='upload', artifact='plugins/missing.zip')
        self.assertIsNone(plugin_detail_metadata(record)['size_bytes'])

    def test_registered_overview_is_used_without_uploaded_template_code(self):
        record = PluginRecord.objects.create(plugin_id='cadevil.example.editor', name='Rust IFC Editor')
        detail = self.client.get(self.url(record))
        self.assertContains(detail, 'The IFC Editor')
        fake = PluginRecord.objects.create(plugin_id='detail.untrusted', package_manifest={'description': '<script>unsafe</script>', 'overview': 'shared/page.html'})
        response = self.client.get(self.url(fake))
        self.assertContains(response, '&lt;script&gt;unsafe&lt;/script&gt;')
        self.assertNotContains(response, '<script>unsafe</script>')

    def test_details_require_login_and_mutations_still_require_csrf(self):
        self.client.auto_csrf = False
        self.assertEqual(self.client.post('/plugins/detail.available/disable/').status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url(), HTTP_HX_REQUEST='true', HTTP_HX_TARGET='plugin-detail-body').status_code, 302)
