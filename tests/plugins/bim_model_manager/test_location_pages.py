"""Owned map lookups, effective markers, HTMX boundaries and CSRF refresh."""
from pathlib import Path
from unittest.mock import patch
import copy
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from tests.bolt_browser import BoltBrowser
from plugin_manager.models import PluginRecord, UserPluginSelection
from plugins.bim_model_manager import PLUGIN_ID
from plugins.bim_model_manager.django.models import FileUpload, BuildingLocation, ModelConversion


GUID = '0' * 22
DATA = {'ifc_sha256':'a'*64, 'buildings':[{'guid':GUID,'name':'Owned building','site_name':'Site',
    'latitude':48.17905555555555,'longitude':16.386166666666664,'status':'located','source':'ifc_site',
    'message':'Declared site origin','crs':'EPSG:4326'}]}
RESULT = {'country':{'country':'AT','label':'Austria','status':'routed','note':'Energy-market routing'},
    'energy':{'status':'unavailable','note':'Provider unavailable','cache':{'notice':'Try again later'}},
    'planning':{'status':'unavailable','message':'Planning service unavailable','missing_information':['Parcel-specific building height']}}


class LocationPageTests(TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        config = override_settings(MEDIA_ROOT=self.folder.name, LOCATION_LOOKUP_CACHE_ROOT=Path(self.folder.name)/'cache')
        config.enable(); self.addCleanup(config.disable)
        self.user = get_user_model().objects.create_user(username='local-owner',password='controlled-local-only')
        self.other = get_user_model().objects.create_user(username='local-other')
        plugin,_ = PluginRecord.objects.update_or_create(plugin_id=PLUGIN_ID,defaults={'enabled':True})
        for user in (self.user,self.other):
            UserPluginSelection.objects.create(user=user,plugin=plugin)
        self.upload = FileUpload.objects.create(user=self.user,description='Private house',document=SimpleUploadedFile('source.ifc',b'original controlled IFC bytes'))
        self.route = reverse('bim:building_context',args=[self.upload.pk,GUID])
        self.client = BoltBrowser(); self.addCleanup(self.client.close); self.client.force_login(self.user)
        locations = patch('plugins.bim_model_manager.location_pages._source_locations',return_value=copy.deepcopy(DATA))
        self.source = locations.start(); self.addCleanup(locations.stop)
        lookup = patch('plugins.bim_model_manager.location_pages.lookup_location',return_value=copy.deepcopy(RESULT))
        self.lookup = lookup.start(); self.addCleanup(lookup.stop)

    def test_owner_gate_and_workflow_run_before_location_or_network_lookup(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.route).status_code,404)
        self.client.logout()
        self.assertEqual(self.client.get(self.route).status_code,302)
        self.client.force_login(self.user)
        UserPluginSelection.objects.filter(user=self.user).delete()
        self.assertEqual(self.client.get(self.route).status_code,404)
        self.source.assert_not_called(); self.lookup.assert_not_called()

    def test_unknown_and_invalid_building_never_request_public_data(self):
        for guid in ['1'*22, 'invalid']:
            self.assertEqual(self.client.get(reverse('bim:building_context',args=[self.upload.pk,guid])).status_code,404)
        self.lookup.assert_not_called()

    def test_full_page_and_map_panel_have_distinct_htmx_boundaries_and_private_cache_headers(self):
        full = self.client.get(self.route)
        self.assertEqual(full.status_code,200)
        self.assertContains(full,'<html')
        self.assertContains(full,'id="content-container"',count=1)
        self.assertIn('no-store',full['Cache-Control'])
        partial = self.client.get(self.route,HTTP_HX_REQUEST='true',HTTP_HX_TARGET='div#building-map-context-17')
        self.assertNotContains(partial,'<html')
        self.assertContains(partial,'id="building-map-context-17"',count=1)
        self.assertNotContains(partial,'id="content-container"')
        self.assertIn('HX-Target',partial['Vary'])
        self.assertTrue(partial.context['inline'])
        self.assertTrue(partial.context['approximate'])

    def test_full_request_and_history_restore_do_not_render_a_panel_only(self):
        for headers in [{'HTTP_HX_REQUEST_TYPE':'full'},{'HTTP_HX_HISTORY_RESTORE_REQUEST':'true'}]:
            response = self.client.get(self.route,HTTP_HX_REQUEST='true',HTTP_HX_TARGET='building-map-context-5',**headers)
            self.assertContains(response,'<html')
            self.assertContains(response,'id="content-container"',count=1)

    def test_refresh_respects_csrf_and_full_page_result_panel_target(self):
        denied = BoltBrowser(csrf=False); self.addCleanup(denied.close); denied.force_login(self.user)
        self.assertEqual(denied.post(self.route,{'action':'refresh'}).status_code,403)
        self.lookup.assert_not_called()
        response = self.client.post(self.route,{'action':'refresh'},HTTP_HX_REQUEST='true',HTTP_HX_TARGET='section#building-context-results')
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,'<html')
        self.assertContains(response,'id="building-context-results"',count=1)
        self.assertTrue(self.lookup.call_args.kwargs['refresh'])
        self.assertFalse(response.context['inline'])
        self.assertEqual(Path(self.upload.document.path).read_bytes(),b'original controlled IFC bytes')

    def test_legacy_target_ids_are_supported_but_other_selectors_cannot_be_reflected(self):
        response = self.client.get(self.route,HTTP_HX_REQUEST='true',HTTP_HX_TARGET='building-map-context-9')
        self.assertContains(response,'id="building-map-context-9"',count=1)
        for value in ['#building-map-context-9','div#building-map-context-9 .bad','script#building-map-context-9']:
            response = self.client.get(self.route,HTTP_HX_REQUEST='true',HTTP_HX_TARGET=value)
            self.assertNotContains(response,'id="building-map-context-9"')
            self.assertContains(response,'id="content-container"',count=1)

    def test_effective_manual_marker_overrides_ifc_and_untrusted_request_coordinates(self):
        BuildingLocation.objects.create(upload=self.upload,guid=GUID,latitude=48.25,longitude=16.45,note='Owner marker')
        response = self.client.get(self.route+'?latitude=0&longitude=0&provider=http://localhost/')
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.lookup.call_args.args[:2],(48.25,16.45))
        self.assertEqual(response.context['building']['source'],'manual')
        self.assertFalse(response.context['approximate'])

    def test_matching_cityjson_geometry_has_the_same_coordinates_as_the_map(self):
        ModelConversion.objects.create(upload=self.upload,source='original.json',source_sha256='b'*64,output_sha256='a'*64,
            metadata={'buildings':[{'guid':GUID,'latitude':48.18,'longitude':16.39,'crs':'EPSG:4326'}]})
        response = self.client.get(self.route)
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.lookup.call_args.args[:2],(48.18,16.39))
        self.assertEqual(response.context['building']['source'],'cityjson_geometry')

    def test_missing_invalid_or_unreadable_location_is_nonfatal_and_avoids_network(self):
        for source in [None, 'unreadable']:
            if source is None:
                empty = copy.deepcopy(DATA); empty['buildings'][0].update(latitude=None,longitude=None,status='missing')
                self.source.return_value = empty
            else:
                self.source.side_effect = OSError('controlled unreadable source')
            response = self.client.get(self.route)
            self.assertEqual(response.status_code,200)
            self.assertContains(response,'Location needed')
            self.lookup.assert_not_called()

    def test_provider_outages_render_readable_results_and_viewer_links(self):
        response = self.client.get(self.route)
        self.assertContains(response,'Provider unavailable')
        self.assertContains(response,'Planning service unavailable')
        self.assertContains(response,reverse('bim:viewer',args=[self.upload.pk]))

    def test_map_rows_supply_an_owned_context_action(self):
        with patch('plugins.bim_model_manager.exchange_pages._source_locations',return_value=copy.deepcopy(DATA)):
            response = self.client.get(reverse('bim:building_map'))
        self.assertEqual(response.context['buildings'][0]['context_url'],self.route)
        self.assertContains(response,'Local data')
