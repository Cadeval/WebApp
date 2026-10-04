from django.test import TestCase
from django.contrib.auth import get_user_model
from django_bolt import BoltAPI
from tests.bolt_browser import BoltBrowser
from config.api import api
from apps.mycelium.api import api as home_api
from apps.plugin_manager.models import PluginRecord
from apps.plugins.bim_model_manager.django.models import FileUpload, ConfigUpload, CalculationConfig, CadevilDocument
from html.parser import HTMLParser


class DemoChoiceMarkup(HTMLParser):
    def __init__(self, markup):
        super().__init__(); self.buttons=[]; self.images=[]; self.feed(markup)
    def handle_starttag(self, tag, attrs):
        values=dict(attrs)
        if tag=='button' and 'data-demo-house-choice' in values: self.buttons.append(values)
        if tag=='img' and 'data-thumbnail-src' in values: self.images.append(values)

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
                self.assertContains(full,'>Open demo</a>',count=1)
            fragment=self.client.get(path,HTTP_HX_REQUEST='true')
            self.assertNotContains(fragment,'<html');self.assertContains(fragment,'id="content-container"',count=1)
            if path == '/demo':
                self.assertContains(fragment,'data-recorded-demo')
            else:
                self.assertNotContains(fragment,'data-recorded-demo')
                self.assertNotContains(fragment,'bim-demo/demo.js')
                self.assertContains(fragment,'hx-get="/demo"',count=1)
            self.assertIn(self.client.post('/demo',{}).status_code,(404,405))
        self.assertEqual(before,[table.objects.count() for table in tables])
        self.assertEqual(self.client.get(f'/plugins/bim/models/{self.private.pk}/viewer/').status_code,302)

    def test_authenticated_home_does_not_autoload_public_demo(self):
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get('/'),'data-recorded-demo')
        self.assertContains(self.client.get('/demo'),'data-recorded-demo')

    def test_house_thumbnail_choices_are_public_lazy_and_disabled_before_geometry_load(self):
        response=self.client.get('/demo',HTTP_HX_REQUEST='true')
        markup=DemoChoiceMarkup(response.content.decode())
        self.assertEqual([button['data-demo-house-choice'] for button in markup.buttons],['0','1','2','3'])
        self.assertTrue(all('disabled' in button and button['aria-pressed']=='false' for button in markup.buttons))
        self.assertEqual(len(markup.images),4)
        self.assertEqual([image['data-thumbnail-src'].rsplit('/',1)[-1] for image in markup.images],
                         ['house-a-thumbnail.png','house-b-thumbnail.png','house-c-thumbnail.png','house-d-thumbnail.png'])
        self.assertTrue(all('src' not in image for image in markup.images))
        self.assertNotContains(response,str(self.private.pk))
        self.assertNotContains(self.client.get('/'),'data-demo-house-choice')
