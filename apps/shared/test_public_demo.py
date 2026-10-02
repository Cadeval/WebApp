from django.test import TestCase
from django.contrib.auth import get_user_model
from django_bolt import BoltAPI
from tests.bolt_browser import BoltBrowser
from config.api import api
from apps.mycelium.api import api as home_api
from apps.plugin_manager.models import PluginRecord
from apps.shared.models import FileUpload, ConfigUpload, CalculationConfig, CadevilDocument

class PublicDemoTests(TestCase):
    def setUp(self):
        combined=BoltAPI(trailing_slash='keep',django_middleware=True);combined.mount('',api);combined.mount('',home_api)
        self.client=BoltBrowser(api=combined);self.addCleanup(self.client.close)
        self.user=get_user_model().objects.create_user(username='demo-owner')
        self.private=FileUpload.objects.create(user=self.user,document='private-model.ifc',description='Private source marker')
        PluginRecord.objects.update_or_create(plugin_id='cadevil.bim.model_manager',defaults={'enabled':False})

    def test_anonymous_home_and_demo_are_public_fragments_and_do_not_touch_user_data(self):
        tables=[FileUpload,ConfigUpload,CalculationConfig,CadevilDocument]
        before=[table.objects.count() for table in tables]
        for path in ['/', '/demo']:
            full=self.client.get(path);self.assertEqual(full.status_code,200)
            self.assertContains(full,'<html');self.assertContains(full,'id="content-container"',count=1)
            self.assertNotContains(full,'Private source marker')
            if path == '/demo':
                self.assertContains(full,'data-recorded-demo')
                self.assertContains(full,'bim-demo/demo.js')
            else:
                self.assertNotContains(full,'data-recorded-demo')
                self.assertNotContains(full,'bim-demo/demo.js')
                self.assertContains(full,'href="/demo"')
            fragment=self.client.get(path,HTTP_HX_REQUEST='true')
            self.assertNotContains(fragment,'<html');self.assertContains(fragment,'id="content-container"',count=1)
            if path == '/demo':
                self.assertContains(fragment,'data-recorded-demo')
            else:
                self.assertNotContains(fragment,'data-recorded-demo')
                self.assertNotContains(fragment,'bim-demo/demo.js')
                self.assertContains(fragment,'hx-get="/demo"')
            self.assertIn(self.client.post('/demo',{}).status_code,(404,405))
        self.assertEqual(before,[table.objects.count() for table in tables])
        self.assertEqual(self.client.get(f'/plugins/bim/models/{self.private.pk}/viewer/').status_code,302)

    def test_authenticated_home_does_not_autoload_public_demo(self):
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get('/'),'data-recorded-demo')
        self.assertContains(self.client.get('/demo'),'data-recorded-demo')
