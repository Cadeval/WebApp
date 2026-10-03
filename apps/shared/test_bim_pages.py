from tests.bolt_browser import BoltBrowser
import csv
import io
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse, resolve

from apps.plugin_manager.models import PluginRecord, UserPluginSelection
from apps.plugin_manager.registry import PluginRegistry, NAV_ITEM_EXTENSION_POINT
from apps.plugins.bim_model_manager import PLUGIN_ID, plugin_manifest
from apps.shared.models import CalculationConfig, ConfigUpload, FileUpload, CadevilDocument, BuildingMetrics
from apps.shared.ifc_extractor.test_material_assessment import IfcPassportTests, reference
from apps.shared.ifc_extractor.material_assessment import load_reference


class BimPageIntegrationTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        setting = override_settings(MEDIA_ROOT=self.directory.name)
        setting.enable(); self.addCleanup(setting.disable)
        self.user = get_user_model().objects.create_user(username='bim-pages', password='test-password')
        self.other = get_user_model().objects.create_user(username='other-pages')
        self.bim_plugin,_=PluginRecord.objects.update_or_create(plugin_id=PLUGIN_ID, defaults={'enabled': True})
        for user in (self.user,self.other):
            UserPluginSelection.objects.create(user=user,plugin=self.bim_plugin)
        self.client = BoltBrowser()
        self.addCleanup(self.client.close)
        self.client.force_login(self.user)

    def reference_file(self):
        records = reference()
        headers = list(dict.fromkeys(k for row in records.values() for k in row))
        stream = io.StringIO(); writer = csv.writer(stream, delimiter=';')
        writer.writerow(['Material'] + headers)
        for material, row in records.items():
            writer.writerow([material] + [row.get(h) for h in headers])
        return SimpleUploadedFile('reference.csv', stream.getvalue().encode())

    def upload_reference(self):
        response = self.client.post(reverse('bim:configuration_library'), {
            'description': 'Original coefficients', 'document': self.reference_file()})
        self.assertRedirects(response, reverse('bim:config_editor'))
        return CalculationConfig.objects.get(user=self.user)

    def test_all_contributed_routes_resolve_and_disabled_plugin_hides_pages(self):
        registry = PluginRegistry(); plugin_manifest().register(registry)
        items = registry.get_active(NAV_ITEM_EXTENSION_POINT, enabled_ids={PLUGIN_ID})
        self.assertEqual(len(items), 6)
        for item in items:
            self.assertFalse(item.full_page)
            resolve(item.url)
            self.assertEqual(self.client.get(item.url).status_code, 200, item.url)
        PluginRecord.objects.filter(plugin_id=PLUGIN_ID).update(enabled=False)
        self.assertEqual(registry.get_active(NAV_ITEM_EXTENSION_POINT, enabled_ids=set()), [])
        for item in items:
            self.assertEqual(self.client.get(item.url).status_code, 404)

    def test_htmx_four_full_request_keeps_the_shell_for_history_restore(self):
        route = reverse('bim:model_manager')
        partial = self.client.get(route, HTTP_HX_REQUEST='true', HTTP_HX_REQUEST_TYPE='partial')
        self.assertNotContains(partial, '<html')
        self.assertContains(partial, 'id="content-container"')
        full = self.client.get(route, HTTP_HX_REQUEST='true', HTTP_HX_REQUEST_TYPE='full')
        self.assertContains(full, '<html')
        self.assertContains(full, 'hx-headers:inherited')
        self.assertIn('HX-Request-Type', full['Vary'])

    def test_anonymous_routes_redirect_to_current_login(self):
        self.client.logout()
        for name in ['model_manager', 'configuration_library', 'config_editor', 'download_csv']:
            response = self.client.get(reverse('bim:' + name))
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith('/mycelium/login?next='))

    def test_full_page_and_htmx_fragment_are_coherent(self):
        response = self.client.get(reverse('bim:config_editor'))
        self.assertContains(response, '<html')
        self.assertContains(response, 'No active configuration')
        self.assertContains(response, 'id="content-container"', count=1)
        fragment = self.client.get(reverse('bim:config_editor'), HTTP_HX_REQUEST='true')
        self.assertNotContains(fragment, '<html')
        self.assertContains(fragment, 'id="content-container"', count=1)
        self.assertIn('HX-Request', fragment.headers['Vary'])

    def test_reference_selection_is_owned_and_bad_reference_does_not_persist(self):
        original = self.upload_reference()
        bad = self.client.post(reverse('bim:configuration_library'), {
            'document': SimpleUploadedFile('broken.xlsx', b'not a spreadsheet')})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(ConfigUpload.objects.count(), 1)
        self.client.force_login(self.other)
        response = self.client.post(reverse('bim:save_config'), {'upload': original.upload_id})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CalculationConfig.objects.filter(user=self.other).exists())

    def test_edit_saves_new_file_preserves_source_and_is_used_by_calculation(self):
        active = self.upload_reference()
        old_upload = active.upload
        old_bytes = Path(old_upload.document.path).read_bytes()
        post = {'source': str(active.upload_id)}
        names = list(active.config['data'])
        for i, material in enumerate(names):
            for j, header in enumerate(active.config['header']):
                value = active.config['data'][material].get(header)
                post[f'cell_{i}_{j}'] = '' if value is None else value
                if header == 'Dichte': post[f'cell_{i}_{j}'] = '1000'
        response = self.client.post(reverse('bim:config_editor'), post)
        self.assertRedirects(response, reverse('bim:config_editor'))
        active.refresh_from_db()
        self.assertNotEqual(active.upload_id, old_upload.pk)
        self.assertEqual(Path(old_upload.document.path).read_bytes(), old_bytes)
        self.assertEqual(ConfigUpload.objects.count(), 2)
        self.assertTrue(all(float(row['Dichte']) == 1000 for row in load_reference(active.upload.document.path).values()))
        exported = self.client.get(reverse('bim:download_csv'))
        self.assertEqual(exported.status_code, 200)
        self.assertIn('attachment;', exported.headers['Content-Disposition'])
        model = Path(self.directory.name) / 'fixture.ifc'; IfcPassportTests().model().write(str(model))
        upload_response = self.client.post(reverse('bim:model_manager'), {
            'document': SimpleUploadedFile('model.ifc', model.read_bytes()), 'description': 'My model'})
        self.assertRedirects(upload_response, reverse('bim:model_manager'))
        upload = FileUpload.objects.get(user=self.user)
        form_response = self.client.get(reverse('material_passport:calculate') + f'?model={upload.pk}')
        self.assertEqual(form_response.context['form'].initial['model'], upload)
        self.assertEqual(form_response.context['form'].initial['reference'], active.upload_id)
        assessment = self.client.post(reverse('material_passport:calculate'), {
            'model': upload.pk, 'reference': active.upload_id, 'years': 100,
            'replacement_boundary': 'inclusive', 'grade_weighting': 'mass', 'lca_averaging': 'installed_mass'})
        self.assertEqual(assessment.status_code, 302)
        metrics = BuildingMetrics.objects.get(project__user=self.user)
        self.assertAlmostEqual(metrics.assessment_report['building']['mass'], 4000)
        self.assertEqual(metrics.assessment_report['options']['years'], 100)
        manager = self.client.get(reverse('bim:model_manager'))
        document = CadevilDocument.objects.get(user=self.user)
        self.assertContains(manager, reverse('material_passport:report', args=[document.pk]))
        self.assertEqual(self.client.post(reverse('bim:delete_model', args=[upload.pk])).status_code, 409)

    def test_stale_or_truncated_editor_submission_cannot_replace_reference(self):
        active = self.upload_reference()
        original = active.upload_id
        for body, expected in [({'source': str(original)}, 400), ({'source': 'stale'}, 409)]:
            self.assertEqual(self.client.post(reverse('bim:config_editor'), body).status_code, expected)
            active.refresh_from_db(); self.assertEqual(active.upload_id, original)
            self.assertEqual(ConfigUpload.objects.count(), 1)

    def test_model_download_delete_and_listing_are_owner_scoped_and_post_only(self):
        file = FileUpload.objects.create(user=self.other, document='other-private.ifc', description='Private model')
        self.assertNotContains(self.client.get(reverse('bim:model_manager')), 'Private model')
        self.assertEqual(self.client.get(reverse('bim:download_model', args=[file.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('bim:delete_model', args=[file.pk])).status_code, 404)
        own = FileUpload.objects.create(user=self.user, document=SimpleUploadedFile('own.ifc', b'IFC source'))
        response = self.client.get(reverse('bim:download_model', args=[own.pk]))
        self.assertEqual(response.content, b'IFC source')
        self.assertIn(self.client.get(reverse('bim:delete_model', args=[own.pk])).status_code, (404, 405))
        self.assertTrue(FileUpload.objects.filter(pk=own.pk).exists())
        self.assertEqual(self.client.post(reverse('bim:delete_model', args=[own.pk])).status_code, 302)
        self.assertFalse(FileUpload.objects.filter(pk=own.pk).exists())
        self.assertTrue(FileUpload.objects.filter(pk=file.pk).exists())

    def test_mutations_require_csrf(self):
        client = BoltBrowser(csrf=False); self.addCleanup(client.close); client.force_login(self.user)
        self.assertEqual(client.post(reverse('bim:configuration_library'), {}).status_code, 403)
        self.assertEqual(client.post(reverse('bim:config_editor'), {}).status_code, 403)

    def test_routes_are_native_and_keep_existing_reverse_names(self):
        from config.api import api
        from django_bolt.urls import build_urlpatterns
        self.assertFalse(api._asgi_mounts)
        self.assertEqual(len(api._routes), 32)
        self.assertTrue(build_urlpatterns(api))
        self.assertEqual(reverse('bim:model_manager'), '/plugins/bim/model_manager/')
        self.assertEqual(reverse('material_passport:calculate'), '/plugins/bim/material-passport/')

    def test_plain_multipart_csrf_token_and_origin_checks(self):
        client = BoltBrowser(csrf=False)
        self.addCleanup(client.close)
        client.force_login(self.user)
        self.assertEqual(client.get(reverse('bim:model_manager')).status_code, 200)
        token = client.transport.cookies['csrftoken']
        response = client.post(reverse('bim:configuration_library'), {
            'description': 'Browser form', 'document': self.reference_file(),
            'csrfmiddlewaretoken': token})
        self.assertEqual(response.status_code, 302)
        for extra in [{'csrfmiddlewaretoken': 'wrong'}, {'csrfmiddlewaretoken': token}]:
            headers = {} if extra['csrfmiddlewaretoken'] == 'wrong' else {'Origin': 'https://foreign.example'}
            response = client.post(reverse('bim:configuration_library'), {
                'document': self.reference_file(), **extra}, headers=headers)
            self.assertEqual(response.status_code, 403)
        self.assertEqual(ConfigUpload.objects.count(), 1)

    def test_all_mutations_reject_missing_csrf_and_owned_reference_can_be_selected(self):
        active = self.upload_reference()
        response = self.client.post(reverse('bim:save_config'), {'upload': active.upload_id})
        self.assertEqual(response.status_code, 302)
        own = FileUpload.objects.create(user=self.user, document=SimpleUploadedFile('own.ifc', b'IFC source'))
        client = BoltBrowser(csrf=False)
        self.addCleanup(client.close)
        client.force_login(self.user)
        paths = [reverse('bim:' + name) for name in
                 ['model_manager', 'configuration_library', 'config_editor', 'save_config']]
        paths += [reverse('bim:delete_model', args=[own.pk]),
                  reverse('material_passport:calculate'), reverse('material_passport:compare')]
        for path in paths:
            self.assertEqual(client.post(path, {}).status_code, 403, path)
        self.assertTrue(FileUpload.objects.filter(pk=own.pk).exists())
        self.assertEqual(ConfigUpload.objects.count(), 1)

    def test_home_menu_renders_enabled_bim_links_on_native_async_route(self):
        from copy import deepcopy
        from django.conf import settings
        from django_bolt import BoltAPI
        from django_bolt.testing import TestClient
        from config.api import api as bim_api
        from apps.mycelium.api import api as home_api
        templates = deepcopy(settings.TEMPLATES)
        templates[0]['OPTIONS']['context_processors'].append(
            'apps.plugin_manager.context_processors.plugin_nav_items')
        combined = BoltAPI(trailing_slash='keep')
        combined.mount('', bim_api); combined.mount('', home_api)
        with override_settings(TEMPLATES=templates), TestClient(combined, base_url='http://testserver') as client:
            client.cookies.set('sessionid', self.client.transport.cookies['sessionid'])
            response = client.get('/')
            self.assertEqual(response.status_code, 200)
            self.assertIn('BIM Model Manager', response.text)
            self.assertIn('href="/plugins/bim/config_editor/"', response.text)
            PluginRecord.objects.filter(plugin_id=PLUGIN_ID).update(enabled=False)
            self.assertNotIn('BIM Model Manager', client.get('/').text)


    def test_native_viewer_geometry_is_owned_and_does_not_modify_source(self):
        import json
        import struct
        source = Path(self.directory.name) / 'viewer.ifc'
        IfcPassportTests().model().write(str(source))
        original = source.read_bytes()
        upload = FileUpload.objects.create(user=self.user,
                    document=SimpleUploadedFile('viewer.ifc', original), description='Viewer source')
        viewer = reverse('bim:viewer', args=[upload.pk])
        geometry = reverse('bim:model_geometry', args=[upload.pk])
        manager = self.client.get(reverse('bim:model_manager'))
        self.assertContains(manager, viewer)
        page = self.client.get(viewer)
        self.assertContains(page, '<html')
        self.assertContains(page, geometry)
        self.assertContains(page, 'id="content-container"', count=1)
        response = self.client.get(geometry)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Type'], 'model/gltf-binary')
        glb = response.content
        self.assertEqual(glb[:4], b'glTF')
        length = struct.unpack_from('<I', glb, 12)[0]
        data = json.loads(glb[20:20+length])
        self.assertGreater(len(data['meshes']), 0)
        self.assertTrue(any(n.get('extras', {}).get('ifcType') == 'IfcWall' for n in data['nodes']))
        self.assertEqual(Path(upload.document.path).read_bytes(), original)
        self.assertEqual(self.client.get(geometry).content, glb)
        self.client.force_login(self.other)
        for path in (viewer, geometry): self.assertEqual(self.client.get(path).status_code, 404)
        self.client.force_login(self.user)
        PluginRecord.objects.filter(plugin_id=PLUGIN_ID).update(enabled=False)
        for path in (viewer, geometry): self.assertEqual(self.client.get(path).status_code, 404)
        self.client.logout()
        for path in (viewer, geometry): self.assertEqual(self.client.get(path).status_code, 302)

    def test_viewer_reports_invalid_or_empty_geometry_without_saving_reports(self):
        broken = FileUpload.objects.create(user=self.user,
                    document=SimpleUploadedFile('broken.ifc', b'not IFC'))
        response = self.client.get(reverse('bim:model_geometry', args=[broken.pk]))
        self.assertEqual(response.status_code, 422)
        self.assertFalse(CadevilDocument.objects.exists())
