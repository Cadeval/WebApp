from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django_bolt import BoltAPI
from tests.bolt_browser import BoltBrowser
from config.api import api as main_api
from apps.mycelium.api import api as home_api
from .api import api as plugin_api
from .models import PluginRecord

class PluginMenuTests(TestCase):
    def setUp(self):
        combined=BoltAPI(trailing_slash='keep',django_middleware=True)
        for api in [main_api,home_api,plugin_api]:combined.mount('',api)
        self.client=BoltBrowser(api=combined);self.addCleanup(self.client.close)
        self.staff=get_user_model().objects.create_user(username='manager-staff',is_staff=True)
        self.regular=get_user_model().objects.create_user(username='manager-user')
        self.url=reverse('plugin_manager:plugin_list')
        self.record=PluginRecord.objects.create(plugin_id='menu-test',name='Menu Test',enabled=False)

    def test_staff_menu_link_and_full_fragment_boundaries(self):
        self.client.force_login(self.staff)
        home=self.client.get('/')
        self.assertContains(home,'hx-get="'+self.url+'"')
        full=self.client.get(self.url)
        self.assertContains(full,'<html')
        self.assertContains(full,'id="content-container"',count=1)
        fragment=self.client.get(self.url,HTTP_HX_REQUEST='true')
        self.assertContains(fragment,'Menu Test')
        self.assertNotContains(fragment,'<html')
        self.assertContains(fragment,'id="content-container"',count=1)

    def test_nonstaff_cannot_access_manager_or_actions(self):
        self.client.force_login(self.regular)
        self.assertNotContains(self.client.get('/'),'href="'+self.url+'"')
        self.assertEqual(self.client.get(self.url).status_code,403)
        action=reverse('plugin_manager:plugin_enable',args=[self.record.plugin_id])
        self.assertEqual(self.client.post(action).status_code,403)
        self.record.refresh_from_db();self.assertFalse(self.record.enabled)

    def test_anonymous_redirects_to_existing_login(self):
        response=self.client.get(self.url)
        self.assertEqual(response.status_code,302)
        self.assertTrue(response.url.startswith('/mycelium/login'))

    def test_staff_toggle_is_post_only_and_returns_refreshed_row(self):
        self.client.force_login(self.staff)
        url=reverse('plugin_manager:plugin_enable',args=[self.record.plugin_id])
        self.assertIn(self.client.get(url).status_code,(404,405))
        self.client.get(self.url)
        def enable(identifier,action):PluginRecord.objects.filter(plugin_id=identifier).update(enabled=True)
        with patch('apps.plugin_manager.api.manage_plugin',side_effect=enable):
            response=self.client.post(url)
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Enabled')

    def test_manager_actions_require_csrf(self):
        self.client.force_login(self.staff)
        self.client.auto_csrf=False
        self.assertEqual(self.client.post(reverse('plugin_manager:plugin_enable',args=[self.record.plugin_id])).status_code,403)
