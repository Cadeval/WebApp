from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from plugins.example_plugin import EXAMPLE_PLUGIN_ID
from plugin_manager.models import PluginRecord


class IfcEditorViewTests(TestCase):
    def setUp(self) -> None:
        self.url = reverse("example_plugin:ifc_editor")
        self.record = PluginRecord.objects.create(
            plugin_id=EXAMPLE_PLUGIN_ID,
            name="Cadevil Rust IFC Editor",
            enabled=True,
        )
        self.user = get_user_model().objects.create_user(
            username="ifc-editor-user",
            password="pw12345678",
        )

    def test_anonymous_user_is_redirected_to_login(self) -> None:
        response = self.client.get(self.url, HTTP_HX_REQUEST="true")
        self.assertRedirects(
            response,
            f"/accounts/login/?next={self.url}",
            fetch_redirect_response=False,
        )

    def test_direct_authenticated_get_without_htmx_redirects_home(self) -> None:
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_authenticated_htmx_get_renders_editor_fragment(self) -> None:
        self.client.force_login(self.user)

        response = self.client.get(self.url, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.count(b'id="content-container"'), 1)
        self.assertContains(response, "data-ifc-editor")
        self.assertContains(
            response,
            'data-worker-url="/static/js/plugins/example_plugin_worker.js"',
        )
        self.assertContains(response, 'data-wasm-url="/static/wasm/example_plugin.wasm"')
        self.assertNotContains(response, "<script")
        self.assertIn("HX-Request", response.headers["Vary"])

    def test_post_requests_are_rejected(self) -> None:
        self.client.force_login(self.user)

        response = self.client.post(self.url, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 405)

    def test_missing_plugin_record_returns_not_found(self) -> None:
        self.record.delete()
        self.client.force_login(self.user)

        response = self.client.get(self.url, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 404)

    def test_disabled_plugin_record_returns_not_found(self) -> None:
        self.record.enabled = False
        self.record.save(update_fields=["enabled"])
        self.client.force_login(self.user)

        response = self.client.get(self.url, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 404)

    def test_plugin_record_with_load_error_returns_not_found(self) -> None:
        self.record.error = "Registration failed"
        self.record.save(update_fields=["error"])
        self.client.force_login(self.user)

        response = self.client.get(self.url, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 404)

    def test_template_scopes_ids_and_accessibility_attributes(self) -> None:
        self.client.force_login(self.user)

        response = self.client.get(self.url, HTTP_HX_REQUEST="true")

        content = response.content.decode()
        self.assertEqual(content.count('id="ifc-editor-title"'), 1)
        self.assertIn('aria-labelledby="ifc-editor-title"', content)
        self.assertIn('aria-describedby="ifc-editor-notes"', content)
        self.assertIn('role="status"', content)
        self.assertIn('aria-live="polite"', content)
        self.assertIn('role="alert"', content)
        self.assertIn('aria-live="assertive"', content)
        self.assertIn('accept=".ifc"', content)
        self.assertIn("never uploaded", content)
        self.assertIn("Save As", content)
