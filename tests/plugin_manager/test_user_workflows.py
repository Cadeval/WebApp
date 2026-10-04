"""User workflow isolation over Bolt's real browser transport."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from plugin_manager.context_processors import plugin_editor_items, plugin_nav_items
from plugin_manager.forms import PluginUploadForm
from plugin_manager.models import PluginRecord, UserPluginSelection
from tests.plugin_manager.package_fixtures import signed_package
from plugin_manager.registry import EditorPlugin, NavItem, PluginRegistry
from plugin_manager.services import create_uploaded_plugin
from tests.plugin_manager import tests as existing_tests
from plugin_manager.workflows import selected_plugin_ids, selectable_plugin, workflow_plugin_enabled
from tests.plugin_manager.test_store import catalog_section


@override_settings(STATIC_URL="/static/", DEBUG=True)
class UserPluginWorkflowTests(TestCase):
    def setUp(self):
        existing_tests.NativePluginTests.setUp(self)
        self.other = get_user_model().objects.create_user(username="other-workflow-user")
        self.available = PluginRecord.objects.create(
            plugin_id="workflow.available", name="Available workflow tool", enabled=True
        )
        self.second = PluginRecord.objects.create(
            plugin_id="workflow.second", name="Second workflow tool", enabled=True
        )

    def login(self, user):
        self.client.logout()
        self.client.force_login(user)
        self.client.get("/plugins/manage/")

    def select(self, user, record):
        return UserPluginSelection.objects.get_or_create(user=user, plugin=record)[0]

    def action(self, record, action="enable", **data):
        return self.client.post(
            f"/plugins/store/{record.plugin_id}/{action}/", data, HTTP_HX_REQUEST="true"
        )

    def request(self, user):
        request = RequestFactory().get("/")
        request.user = user
        return request

    def uploaded(self, plugin_id="workflow.uploaded"):
        form = PluginUploadForm(
            {},
            {
                "artifact": SimpleUploadedFile(
                    "workflow.zip",
                    signed_package(self.staff, plugin_id),
                    content_type="application/zip",
                )
            },
        )
        self.assertTrue(form.is_valid(), form.errors)
        record = create_uploaded_plugin(form, self.staff)
        record.set_enabled(True)
        return record

    def test_available_plugins_are_opt_in_even_for_staff(self):
        for user in (self.regular, self.other, self.staff, AnonymousUser()):
            with self.subTest(user=str(user)):
                self.assertEqual(set(selected_plugin_ids(user)), set())
                self.assertFalse(workflow_plugin_enabled(user, self.available.plugin_id))

    def test_selections_belong_to_each_user_and_ignore_forged_user_fields(self):
        self.login(self.regular)
        response = self.action(self.available, user=self.other.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(selected_plugin_ids(self.regular)), {self.available.plugin_id})
        self.assertEqual(set(selected_plugin_ids(self.other)), set())
        self.assertEqual(set(selected_plugin_ids(self.staff)), set())
        self.assertTrue(workflow_plugin_enabled(self.regular, self.available.plugin_id))
        self.available.refresh_from_db()
        self.assertTrue(self.available.enabled)

    def test_repeated_personal_selection_is_idempotent(self):
        self.login(self.regular)
        for _ in range(2):
            self.assertEqual(self.action(self.available).status_code, 200)
        self.assertEqual(
            UserPluginSelection.objects.filter(user=self.regular, plugin=self.available).count(),
            1,
        )
        for _ in range(2):
            self.assertEqual(self.action(self.available, "disable").status_code, 200)
        self.assertFalse(UserPluginSelection.objects.filter(user=self.regular).exists())
        self.available.refresh_from_db()
        self.assertTrue(self.available.enabled)

    def test_personal_disable_preserves_other_users_and_global_state(self):
        self.select(self.regular, self.available)
        self.select(self.other, self.available)
        self.login(self.regular)
        self.assertEqual(self.action(self.available, "disable").status_code, 200)
        self.assertFalse(workflow_plugin_enabled(self.regular, self.available.plugin_id))
        self.assertTrue(workflow_plugin_enabled(self.other, self.available.plugin_id))
        self.available.refresh_from_db()
        self.assertTrue(self.available.enabled)

    def test_manager_catalog_and_personal_store_do_not_mix_users(self):
        self.select(self.other, self.second)
        self.login(self.regular)
        self.assertContains(self.client.get("/plugins/manage/"), self.available.name)
        self.assertContains(self.client.get("/plugins/manage/"), self.second.name)
        store = self.client.get("/plugins/manage/")
        self.assertNotIn(self.available.name, catalog_section(store, 'my-workflow-title'))
        self.assertNotIn(self.second.name, catalog_section(store, 'my-workflow-title'))
        self.assertIn(self.available.name, catalog_section(store, 'available-tools-title'))
        self.assertIn(self.second.name, catalog_section(store, 'available-tools-title'))
        self.assertEqual(self.action(self.available).status_code, 200)
        store = self.client.get("/plugins/manage/")
        self.assertIn(self.available.name, catalog_section(store, 'my-workflow-title'))
        self.assertNotIn(self.second.name, catalog_section(store, 'my-workflow-title'))
        self.assertNotIn(self.available.name, catalog_section(store, 'available-tools-title'))
        self.assertIn(self.second.name, catalog_section(store, 'available-tools-title'))
        self.login(self.other)
        store = self.client.get("/plugins/manage/")
        self.assertIn(self.second.name, catalog_section(store, 'my-workflow-title'))
        self.assertNotIn(self.available.name, catalog_section(store, 'my-workflow-title'))
        self.assertNotIn(self.second.name, catalog_section(store, 'available-tools-title'))
        self.assertIn(self.available.name, catalog_section(store, 'available-tools-title'))

    def test_regular_manager_has_only_available_tools_and_staff_sees_global_controls(self):
        unavailable = [
            PluginRecord.objects.create(plugin_id="workflow.disabled", name="Disabled workflow tool", enabled=False),
            PluginRecord.objects.create(plugin_id="workflow.failed", name="Failed workflow tool", enabled=True, error="Discovery failed"),
            PluginRecord.objects.create(plugin_id="workflow.production", name="Production workflow tool", enabled=True, compatibility="production"),
            PluginRecord.objects.create(plugin_id="cadevil.mcp.context7", name="Debug MCP workflow tool", enabled=True, compatibility="debug"),
            PluginRecord.objects.create(plugin_id="cadevil.mcp.docker", name="Docker inspection service", enabled=True, compatibility="debug"),
        ]
        self.login(self.regular)
        response = self.client.get("/plugins/manage/")
        self.assertContains(response, self.available.name)
        for record in unavailable:
            self.assertNotContains(response, record.name)
        self.assertNotContains(response, f'/plugins/{self.available.plugin_id}/disable/')
        self.login(self.staff)
        response = self.client.get("/plugins/manage/")
        for record in unavailable:
            self.assertContains(response, record.name)
        self.assertContains(response, f'/plugins/{self.available.plugin_id}/disable/')

    def test_docker_service_rejects_personal_selection_for_regular_and_staff_users(self):
        docker = PluginRecord.objects.create(plugin_id="cadevil.mcp.docker",
            name="Docker inspection service", enabled=True, compatibility="debug")
        for user in (self.regular, self.staff):
            with self.subTest(staff=user.is_staff):
                self.login(user)
                self.assertEqual(self.action(docker).status_code, 409)
                self.assertFalse(UserPluginSelection.objects.filter(user=user, plugin=docker).exists())
        # An old or forged stored choice cannot bypass the administrator-only
        # service boundary or add this provider to a personal workflow.
        self.select(self.regular, docker)
        self.assertFalse(selectable_plugin(docker))
        self.assertNotIn(docker.plugin_id, selected_plugin_ids(self.regular))
        self.assertFalse(workflow_plugin_enabled(self.regular, docker.plugin_id))
        self.login(self.regular)
        self.assertNotContains(self.client.get('/plugins/manage/'), docker.name)

    def test_manager_and_store_keep_full_and_htmx_boundaries(self):
        self.login(self.regular)
        self.select(self.regular, self.available)
        full = self.client.get('/plugins/manage/')
        self.assertContains(full, "<html")
        self.assertContains(full, 'id="content-container"', count=1)
        legacy = self.client.get('/plugins/store/')
        self.assertEqual(legacy.status_code, 302)
        self.assertEqual(legacy.url, '/plugins/manage/')
        for route in ('/plugins/manage/', '/plugins/store/'):
            with self.subTest(route=route):
                fragment = self.client.get(route, HTTP_HX_REQUEST="true")
                self.assertNotContains(fragment, "<html")
                self.assertContains(fragment, 'id="content-container"', count=1)
                self.assertIn(self.available.name, catalog_section(fragment, 'my-workflow-title'))
                if route == '/plugins/store/':
                    self.assertEqual(fragment['HX-Replace-Url'], '/plugins/manage/')

    def test_regular_users_cannot_change_global_state(self):
        self.login(self.regular)
        for action in ("enable", "disable"):
            self.assertEqual(
                self.client.post(f"/plugins/{self.available.plugin_id}/{action}/").status_code,
                403,
            )
        self.available.refresh_from_db()
        self.assertTrue(self.available.enabled)
        self.assertFalse(UserPluginSelection.objects.filter(user=self.regular).exists())

    def test_personal_actions_require_authentication_csrf_and_post(self):
        self.login(self.regular)
        for action in ("enable", "disable"):
            route = f"/plugins/store/{self.available.plugin_id}/{action}/"
            self.assertIn(self.client.get(route).status_code, (404, 405))
            self.client.auto_csrf = False
            self.assertEqual(self.client.post(route).status_code, 403)
        self.assertFalse(UserPluginSelection.objects.filter(user=self.regular).exists())
        self.client.auto_csrf = True
        self.client.logout()
        self.assertIn(self.action(self.available).status_code, (302, 403))
        self.assertFalse(UserPluginSelection.objects.exists())
        for route in ("/plugins/manage/", "/plugins/store/"):
            self.assertEqual(self.client.get(route).status_code, 302)

    def test_unknown_actions_or_plugin_ids_never_create_selections(self):
        self.login(self.regular)
        self.assertEqual(self.action(self.available, "reload").status_code, 404)
        self.assertEqual(self.client.post("/plugins/store/missing-workflow/enable/").status_code, 404)
        self.assertFalse(UserPluginSelection.objects.filter(user=self.regular).exists())

    def test_global_disable_blocks_every_selected_user_without_erasing_choices(self):
        self.select(self.regular, self.available)
        self.select(self.other, self.available)
        self.login(self.staff)
        response = self.client.post(f"/plugins/{self.available.plugin_id}/disable/", HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        for user in (self.regular, self.other):
            self.assertFalse(workflow_plugin_enabled(user, self.available.plugin_id))
            self.assertNotIn(self.available.plugin_id, selected_plugin_ids(user))
            self.assertTrue(UserPluginSelection.objects.filter(user=user, plugin=self.available).exists())
        self.available.set_enabled(True)
        for user in (self.regular, self.other):
            self.assertTrue(workflow_plugin_enabled(user, self.available.plugin_id))

    def test_existing_choice_cannot_bypass_errors_or_environment_policy(self):
        self.select(self.regular, self.available)
        self.available.error = "Discovery failed after selection"
        self.available.save()
        self.assertFalse(workflow_plugin_enabled(self.regular, self.available.plugin_id))
        self.available.error = ""
        self.available.compatibility = "production"
        self.available.save()
        self.assertFalse(workflow_plugin_enabled(self.regular, self.available.plugin_id))
        with override_settings(DEBUG=False):
            self.assertTrue(workflow_plugin_enabled(self.regular, self.available.plugin_id))

    def test_unavailable_and_mcp_plugins_cannot_be_selected(self):
        invalid = [
            PluginRecord.objects.create(plugin_id="workflow.off", enabled=False),
            PluginRecord.objects.create(plugin_id="workflow.error", enabled=True, error="Broken"),
            PluginRecord.objects.create(plugin_id="workflow.wrong-environment", enabled=True, compatibility="production"),
            PluginRecord.objects.create(plugin_id="cadevil.mcp.git", enabled=True, compatibility="debug"),
            PluginRecord.objects.create(plugin_id="workflow.unsigned", source="upload", artifact_type="zip", enabled=True, artifact="plugins/unsigned.zip"),
        ]
        self.login(self.regular)
        for record in invalid:
            with self.subTest(plugin=record.plugin_id):
                self.assertFalse(selectable_plugin(record))
                self.assertEqual(self.action(record).status_code, 409)
                self.assertFalse(UserPluginSelection.objects.filter(user=self.regular, plugin=record).exists())
        self.assertTrue(selectable_plugin(self.available))

    def test_nav_and_editor_contributions_are_filtered_per_user(self):
        local = PluginRegistry()
        for record in (self.available, self.second):
            local.register_nav_item(record.plugin_id, NavItem(record.name, f"/tools/{record.plugin_id}/"))
            local.register_editor_plugin(
                record.plugin_id,
                EditorPlugin(record.plugin_id, record.name, "Workflow tool", "/static/worker.js"),
            )
        self.select(self.regular, self.available)
        self.select(self.other, self.second)
        with patch("plugin_manager.context_processors.registry", local), patch.object(local, "refresh_if_changed"):
            for user, expected in ((self.regular, self.available), (self.other, self.second)):
                request = self.request(user)
                self.assertEqual([item.label for item in plugin_nav_items(request)["plugin_nav_items"]], [expected.name])
                self.assertEqual([item.id for item in plugin_editor_items(request)["plugin_editor_items"]], [expected.plugin_id])
            request = self.request(AnonymousUser())
            self.assertEqual(plugin_nav_items(request)["plugin_nav_items"], [])
            self.assertEqual(plugin_editor_items(request)["plugin_editor_items"], [])

    def test_uploaded_worker_assets_require_the_calling_users_selection(self):
        record = self.uploaded()
        path = f"/plugins/{record.plugin_id}/assets/worker.js"
        self.login(self.regular)
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.action(record).status_code, 200)
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertIn(b"postMessage", response.content)
        self.login(self.other)
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.action(record).status_code, 200)
        self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.action(record, "disable").status_code, 200)
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertTrue(workflow_plugin_enabled(self.regular, record.plugin_id))

    def test_uploaded_navigation_follows_personal_and_global_availability(self):
        record = self.uploaded()
        url = f"/plugins/workflows/{record.plugin_id}/"
        local = PluginRegistry()

        def links(user):
            return plugin_nav_items(self.request(user))["plugin_nav_items"]

        with patch("plugin_manager.context_processors.registry", local), patch.object(local, "refresh_if_changed"):
            self.assertNotIn(url, [item.url for item in links(self.regular)])
            self.select(self.regular, record)
            self.assertEqual([(item.label, item.url) for item in links(self.regular)], [(record.name, url)])
            for user in (self.other, AnonymousUser()):
                self.assertNotIn(url, [item.url for item in links(user)])

            self.login(self.regular)
            self.assertEqual(self.action(record, "disable").status_code, 200)
            self.assertNotIn(url, [item.url for item in links(self.regular)])

            self.select(self.regular, record)
            record.set_enabled(False)
            self.assertNotIn(url, [item.url for item in links(self.regular)])
            record.set_enabled(True)
            self.assertIn(url, [item.url for item in links(self.regular)])

            record.signing_key.revoked_at = timezone.now()
            record.signing_key.save()
            self.assertNotIn(url, [item.url for item in links(self.regular)])
            self.assertTrue(UserPluginSelection.objects.filter(user=self.regular, plugin=record).exists())

    def test_generic_workflow_opens_only_its_selected_worker_without_bim(self):
        record = self.uploaded()
        second = self.uploaded("workflow.other-upload")
        PluginRecord.objects.filter(plugin_id="cadevil.bim.model_manager").update(enabled=False)
        self.select(self.regular, record)
        self.select(self.regular, second)
        self.login(self.regular)
        path = f"/plugins/workflows/{record.plugin_id}/"
        full = self.client.get(path)
        self.assertContains(full, "<html")
        self.assertContains(full, f'data-editor-plugin="{record.plugin_id}"')
        self.assertNotContains(full, f'data-editor-plugin="{second.plugin_id}"')
        fragment = self.client.get(path, HTTP_HX_REQUEST="true")
        self.assertNotContains(fragment, "<html")
        self.assertContains(fragment, 'id="content-container"', count=1)
        self.assertContains(fragment, f'data-worker-url="/plugins/{record.plugin_id}/assets/worker.js"')
        self.login(self.other)
        self.assertEqual(self.client.get(path).status_code, 404)

    def test_signed_upload_revocation_removes_runtime_contributions_and_asset_access(self):
        record = self.uploaded()
        self.select(self.regular, record)
        self.login(self.regular)
        request = self.request(self.regular)
        with patch("plugin_manager.context_processors.registry", PluginRegistry()):
            self.assertEqual([item.id for item in plugin_editor_items(request)["plugin_editor_items"]], [record.plugin_id])
            record.signing_key.revoked_at = timezone.now()
            record.signing_key.save()
            self.assertFalse(workflow_plugin_enabled(self.regular, record.plugin_id))
            self.assertEqual(plugin_editor_items(request)["plugin_editor_items"], [])
        self.assertEqual(self.client.get(f"/plugins/{record.plugin_id}/assets/worker.js").status_code, 404)
        self.assertTrue(UserPluginSelection.objects.filter(user=self.regular, plugin=record).exists())

    def test_browser_and_bim_routes_require_selection_even_for_staff(self):
        tools = (
            ("cadevil.example.editor", "/plugins/ifc-editor/"),
            ("cadevil.rust-example.editor", "/plugins/rust-snake/"),
            ("cadevil.bim.model_manager", "/plugins/bim/model_manager/"),
        )
        for plugin_id, path in tools:
            record, _ = PluginRecord.objects.update_or_create(
                plugin_id=plugin_id, defaults={"enabled": True, "error": "", "compatibility": "both"}
            )
            for user in (self.regular, self.staff):
                with self.subTest(plugin=plugin_id, user=user.username):
                    self.login(user)
                    self.assertEqual(self.client.get(path).status_code, 404)
                    self.select(user, record)
                    response = self.client.get(path, HTTP_HX_REQUEST="true")
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, 'id="content-container"', count=1)
                    self.assertNotContains(response, "<html")
                    self.action(record, "disable")
                    self.assertEqual(self.client.get(path).status_code, 404)
