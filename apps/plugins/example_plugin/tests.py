"""Runtime access checks for the editor over native Bolt transport."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from apps.plugin_manager.models import PluginRecord, UserPluginSelection
from tests.bolt_browser import BoltBrowser


@override_settings(STATIC_URL='/static/')
class IfcEditorViewTests(TestCase):
    def setUp(self):
        self.record = PluginRecord.objects.create(plugin_id='cadevil.example.editor', name='IFC editor', enabled=True)
        self.user = get_user_model().objects.create_user(username='editor-user')
        self.other = get_user_model().objects.create_user(username='other-editor-user')
        UserPluginSelection.objects.create(user=self.user, plugin=self.record)
        self.client = BoltBrowser()
        self.addCleanup(self.client.close)
        self.url = '/plugins/ifc-editor/'

    def test_anonymous_is_redirected_to_session_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('/mycelium/login?next='))

    def test_unselected_user_cannot_use_another_users_editor(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_full_and_fragment_navigation_use_the_same_editor(self):
        self.client.force_login(self.user)
        full = self.client.get(self.url)
        fragment = self.client.get(self.url, HTTP_HX_REQUEST='true')
        self.assertContains(full, '<!DOCTYPE html>', html=False)
        self.assertNotContains(fragment, '<!DOCTYPE html>')
        for response in (full, fragment):
            self.assertContains(response, 'data-ifc-editor')
            self.assertEqual(response.content.count(b'id="content-container"'), 1)

    def test_admin_disable_and_error_revoke_access_immediately(self):
        self.client.force_login(self.user)
        for changes in ({'enabled': False}, {'enabled': True, 'error': 'Registration failed'}):
            PluginRecord.objects.filter(pk=self.record.pk).update(**changes)
            self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertTrue(UserPluginSelection.objects.filter(user=self.user, plugin=self.record).exists())

    def test_post_cannot_mutate_the_client_only_editor(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.assertTrue(UserPluginSelection.objects.filter(user=self.user, plugin=self.record).exists())
