"""Native thumbnail route access and nonfatal selection behavior."""
import tempfile
import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
from asgiref.sync import async_to_sync, sync_to_async
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.http import HttpResponse
from django.urls import reverse

from tests.bolt_browser import BoltBrowser
from plugin_manager.models import PluginRecord, UserPluginSelection
from plugins.bim_model_manager import PLUGIN_ID
from plugins.bim_model_manager.django.models import FileUpload


GUID = '00tMo7QcxqWdIGvc4sMN2A'


class ThumbnailPageTests(TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        settings = override_settings(MEDIA_ROOT=self.folder / 'media',
                                     BUILDING_THUMBNAIL_CACHE_ROOT=self.folder / 'previews')
        settings.enable(); self.addCleanup(settings.disable)
        self.user = get_user_model().objects.create_user(username='thumbnail-owner')
        self.other = get_user_model().objects.create_user(username='thumbnail-other')
        self.plugin, _ = PluginRecord.objects.update_or_create(plugin_id=PLUGIN_ID, defaults={'enabled': True})
        for user in (self.user, self.other):
            UserPluginSelection.objects.create(user=user, plugin=self.plugin)
        self.upload = FileUpload.objects.create(user=self.user, description='Owned building',
            document=SimpleUploadedFile('source.ifc', b'unchanged source bytes'))
        self.route = reverse('bim:model_thumbnail', args=[self.upload.pk])
        self.client = BoltBrowser(); self.addCleanup(self.client.close)
        self.client.force_login(self.user)
        self.png = self.folder / 'preview.png'
        Image.new('RGB', (320, 200), '#e8e9eb').save(self.png)
        render = patch('plugins.bim_model_manager.building_thumbnails.model_thumbnail', return_value=self.png)
        self.render = render.start(); self.addCleanup(render.stop)

    def test_ownership_and_workflow_are_checked_before_source_or_cache_access(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.route).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self.route).status_code, 302)
        self.client.force_login(self.user)
        UserPluginSelection.objects.filter(user=self.user).delete()
        self.assertEqual(self.client.get(self.route).status_code, 404)
        self.render.assert_not_called()

    def test_private_png_and_selected_building_keep_original_source_unchanged(self):
        for query, guid in [('', ''), ('?building=' + GUID, GUID)]:
            response = self.client.get(self.route + query)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Content-Type'], 'image/png')
            self.assertIn('no-store', response['Cache-Control'])
            self.assertIn('Cookie', response['Vary'])
            self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
            self.assertGreater(int(response['Content-Length']), 0)
            self.assertEqual(response.content, self.png.read_bytes())
            self.assertTrue(response.content.startswith(b'\x89PNG\r\n\x1a\n'))
            self.assertEqual(self.render.call_args.kwargs['building_guid'], guid)
            self.assertEqual(Path(self.upload.document.path).read_bytes(), b'unchanged source bytes')

    def test_malformed_building_and_generation_failure_leave_model_selection_usable(self):
        for value in ['../source', 'invalid', '9' * 22]:
            self.assertEqual(self.client.get(self.route + '?building=' + value).status_code, 400)
        self.render.assert_not_called()
        self.render.side_effect = RuntimeError('controlled render failure')
        response = self.client.get(self.route)
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, 'Preview unavailable', status_code=422)
        self.assertNotContains(response, 'controlled render failure', status_code=422)
        manager = self.client.get(reverse('bim:model_manager'))
        self.assertContains(manager, 'Owned building')
        self.assertContains(manager, reverse('bim:viewer', args=[self.upload.pk]))

    def test_menus_only_supply_preview_urls_without_rendering_geometry(self):
        manager = self.client.get(reverse('bim:model_manager'), HTTP_HX_REQUEST='true')
        self.assertContains(manager, self.route)
        self.assertContains(manager, 'data-building-thumbnail')
        locations = {'ifc_sha256': 'a' * 64, 'buildings': [{'guid': GUID, 'name': 'Building',
                     'site_name': 'Site', 'latitude': None, 'longitude': None, 'source': '',
                     'status': 'missing', 'message': '', 'crs': ''}]}
        with patch('plugins.bim_model_manager.exchange_pages._source_locations', return_value=locations):
            map_page = self.client.get(reverse('bim:building_map'))
        self.assertEqual(map_page.context['buildings'][0]['thumbnail_url'], self.route + '?building=' + GUID)
        self.render.assert_not_called()

    def test_cold_preparation_leaves_django_navigation_lane_available(self):
        from plugins.bim_model_manager.thumbnail_pages import model_thumbnail
        started, release = threading.Event(), threading.Event()
        adapted = SimpleNamespace(close=Mock())

        def slow_preview(*_):
            started.set()
            if not release.wait(3):
                raise RuntimeError('The concurrency check did not release its worker.')
            return HttpResponse(self.png.read_bytes(), content_type='image/png')

        async def probe():
            task = asyncio.create_task(model_thumbnail(object(), self.upload.pk))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                ready = await asyncio.wait_for(sync_to_async(lambda: 'navigation ready',
                                                            thread_sensitive=True)(), timeout=1)
                self.assertEqual(ready, 'navigation ready')
                self.assertFalse(task.done())
            finally:
                release.set()
                await task

        with patch('plugins.bim_model_manager.thumbnail_pages.form_request', return_value=adapted), \
             patch('plugins.bim_model_manager.thumbnail_pages._resolve_thumbnail', return_value=(self.upload, '')), \
             patch('plugins.bim_model_manager.thumbnail_pages._render_thumbnail', side_effect=slow_preview):
            async_to_sync(probe)()
        adapted.close.assert_called_once()
