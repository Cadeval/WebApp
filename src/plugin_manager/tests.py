import re
import unittest.mock
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from plugins import EXAMPLE_PLUGIN_ID, plugin_manifest
from plugin_manager.context_processors import plugin_editor_items, plugin_nav_items
from plugin_manager.manifest import (
    PLUGIN_API_VERSION,
    PluginManifest,
    is_api_version_compatible,
)
from plugin_manager.models import PluginRecord
from plugin_manager.registry import (
    EDITOR_PLUGIN_EXTENSION_POINT,
    NAV_ITEM_EXTENSION_POINT,
    DiscoveryResult,
    EditorPlugin,
    NavItem,
    registry,
)
from plugin_manager.services import manage_plugin, reload_plugins
from plugins import RUST_EXAMPLE_PLUGIN_ID
from plugins import plugin_manifest as rust_plugin_manifest


class _FakeEntryPoint:
    """Minimal stand-in for ``importlib.metadata.EntryPoint`` used in tests.

    Only the ``name`` attribute and ``load()`` method are required by
    ``PluginRegistry.discover_plugins``, so we avoid depending on real
    package metadata to exercise discovery.
    """

    def __init__(self, name, loader):
        self.name = name
        self._loader = loader

    def load(self):
        return self._loader()


def _make_manifest(plugin_id, register=None, api_version=PLUGIN_API_VERSION, priority=100):
    return PluginManifest(
        id=plugin_id,
        name=f"Plugin {plugin_id}",
        version="1.0.0",
        api_version=api_version,
        priority=priority,
        register=register,
    )


class ApiVersionCompatibilityTests(TestCase):
    def test_same_major_version_is_compatible(self) -> None:
        self.assertTrue(is_api_version_compatible("1.0", supported="1.3"))

    def test_different_major_version_is_incompatible(self) -> None:
        self.assertFalse(is_api_version_compatible("2.0", supported="1.0"))

    def test_invalid_version_string_is_incompatible(self) -> None:
        self.assertFalse(is_api_version_compatible("not-a-version", supported="1.0"))


class PluginRegistryDiscoveryTests(TestCase):
    def setUp(self) -> None:
        registry.reset()

    def tearDown(self) -> None:
        registry.reset()

    def test_discover_registers_nav_item_and_creates_plugin_record(self) -> None:
        def register(reg) -> None:
            reg.register_nav_item("alpha", NavItem(label="Alpha", url="/alpha/", icon="fa-cube", priority=5))

        entry_points = [_FakeEntryPoint("alpha", lambda: _make_manifest("alpha", register=register))]

        results = registry.discover_and_sync(entry_points)

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].ok)

        record = PluginRecord.objects.get(plugin_id="alpha")
        self.assertTrue(record.enabled)
        self.assertEqual(record.error, "")

        active_nav_items = registry.get_active(NAV_ITEM_EXTENSION_POINT)
        self.assertEqual(len(active_nav_items), 1)
        self.assertEqual(active_nav_items[0].label, "Alpha")

    def test_duplicate_plugin_id_is_rejected_and_first_registration_wins(self) -> None:
        def register_first(reg) -> None:
            reg.register_nav_item("dup", NavItem(label="First", url="/first/"))

        def register_second(reg) -> None:
            reg.register_nav_item("dup", NavItem(label="Second", url="/second/"))

        entry_points = [
            _FakeEntryPoint("first", lambda: _make_manifest("dup", register=register_first)),
            _FakeEntryPoint("second", lambda: _make_manifest("dup", register=register_second)),
        ]

        results = registry.discover_and_sync(entry_points)

        ok_results = [result for result in results if result.ok]
        failed_results = [result for result in results if not result.ok]
        self.assertEqual(len(ok_results), 1)
        self.assertEqual(len(failed_results), 1)
        self.assertIn("Duplicate plugin id", failed_results[0].error)

        active_nav_items = registry.get_active(NAV_ITEM_EXTENSION_POINT)
        self.assertEqual(len(active_nav_items), 1)
        self.assertEqual(active_nav_items[0].label, "First")

    def test_incompatible_api_version_is_recorded_as_error_and_disabled(self) -> None:
        entry_points = [
            _FakeEntryPoint("legacy", lambda: _make_manifest("legacy", api_version="99.0")),
        ]

        results = registry.discover_and_sync(entry_points)

        self.assertFalse(results[0].ok)
        record = PluginRecord.objects.get(plugin_id="legacy")
        self.assertFalse(record.enabled)
        self.assertNotEqual(record.error, "")

    def test_hook_error_is_isolated_and_does_not_break_other_plugins(self) -> None:
        def broken_register(reg) -> None:
            raise RuntimeError("boom")

        def good_register(reg) -> None:
            reg.register_nav_item("good", NavItem(label="Good", url="/good/"))

        entry_points = [
            _FakeEntryPoint("broken", lambda: _make_manifest("broken", register=broken_register)),
            _FakeEntryPoint("good", lambda: _make_manifest("good", register=good_register)),
        ]

        results = registry.discover_and_sync(entry_points)

        by_id = {result.plugin_id: result for result in results}
        self.assertFalse(by_id["broken"].ok)
        self.assertIn("boom", by_id["broken"].error)
        self.assertTrue(by_id["good"].ok)

        active_nav_items = registry.get_active(NAV_ITEM_EXTENSION_POINT)
        self.assertEqual([item.label for item in active_nav_items], ["Good"])

    def test_unloadable_entry_point_does_not_raise(self) -> None:
        def broken_loader():
            raise ImportError("module missing")

        entry_points = [_FakeEntryPoint("missing", broken_loader)]

        results = registry.discover_and_sync(entry_points)

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].ok)
        self.assertIn("module missing", results[0].error)

    def test_get_active_respects_enabled_flag_and_priority_order(self) -> None:
        registry.register_nav_item("low-priority", NavItem(label="Low", url="/low/", priority=50))
        registry.register_nav_item("high-priority", NavItem(label="High", url="/high/", priority=1))
        registry.register_nav_item("disabled-plugin", NavItem(label="Disabled", url="/disabled/", priority=0))

        PluginRecord.objects.create(plugin_id="low-priority", enabled=True)
        PluginRecord.objects.create(plugin_id="high-priority", enabled=True)
        PluginRecord.objects.create(plugin_id="disabled-plugin", enabled=False)

        active = registry.get_active(NAV_ITEM_EXTENSION_POINT)

        self.assertEqual([item.label for item in active], ["High", "Low"])

    def test_discover_registers_typed_editor_plugin(self) -> None:
        def register(reg) -> None:
            reg.register_editor_plugin(
                "editor-demo",
                EditorPlugin(
                    id="editor-demo",
                    name="Editor Demo",
                    description="Runs outside the main thread.",
                    worker_url="/static/plugins/editor-demo/worker.js",
                    wasm_url="/static/plugins/editor-demo/demo.wasm",
                    priority=5,
                ),
            )

        results = registry.discover_and_sync(
            [_FakeEntryPoint("editor-demo", lambda: _make_manifest("editor-demo", register=register))]
        )

        self.assertTrue(results[0].ok)
        editor_plugins = registry.get_active(EDITOR_PLUGIN_EXTENSION_POINT)
        self.assertEqual([plugin.id for plugin in editor_plugins], ["editor-demo"])


class PluginRecordModelTests(TestCase):
    def test_str_uses_name_when_available(self) -> None:
        record = PluginRecord.objects.create(plugin_id="sample", name="Sample Plugin")
        self.assertIn("Sample Plugin", str(record))

    def test_has_error_property(self) -> None:
        record = PluginRecord.objects.create(plugin_id="sample")
        self.assertFalse(record.has_error)
        record.error = "boom"
        self.assertTrue(record.has_error)


class PluginNavigationContextProcessorTests(TestCase):
    def setUp(self) -> None:
        registry.reset()

    def tearDown(self) -> None:
        registry.reset()

    def test_returns_active_nav_items_for_enabled_plugins(self) -> None:
        registry.register_nav_item("demo", NavItem(label="Demo", url="/demo/"))
        PluginRecord.objects.create(plugin_id="demo", enabled=True)

        request = RequestFactory().get("/")
        context = plugin_nav_items(request)

        self.assertEqual(len(context["plugin_nav_items"]), 1)
        self.assertEqual(context["plugin_nav_items"][0].label, "Demo")

    def test_home_page_renders_active_plugin_nav_item_for_logged_in_user(self) -> None:
        registry.register_nav_item("demo", NavItem(label="Demo Nav Plugin", url="/demo-plugin/"))
        PluginRecord.objects.create(plugin_id="demo", enabled=True)

        user = get_user_model().objects.create_user(username="navtester", password="pw12345678")
        self.client.force_login(user)

        response = self.client.get("/")

        self.assertContains(response, "Demo Nav Plugin")
        self.assertContains(response, "/demo-plugin/")


class PluginEditorContextProcessorTests(TestCase):
    def setUp(self) -> None:
        registry.reset()

    def tearDown(self) -> None:
        registry.reset()

    def test_returns_only_active_editor_plugins(self) -> None:
        enabled = EditorPlugin(
            id="enabled",
            name="Enabled",
            description="Enabled plugin",
            worker_url="/enabled.js",
            wasm_url="/enabled.wasm",
        )
        disabled = EditorPlugin(
            id="disabled",
            name="Disabled",
            description="Disabled plugin",
            worker_url="/disabled.js",
            wasm_url="/disabled.wasm",
        )
        registry.register_editor_plugin("enabled", enabled)
        registry.register_editor_plugin("disabled", disabled)
        PluginRecord.objects.create(plugin_id="enabled", enabled=True)
        PluginRecord.objects.create(plugin_id="disabled", enabled=False)

        context = plugin_editor_items(RequestFactory().get("/config_editor/"))

        self.assertEqual(context["plugin_editor_items"], [enabled])

    def test_config_editor_renders_host_managed_plugin_controls(self) -> None:
        editor_plugin = EditorPlugin(
            id="render-demo",
            name="Render Demo",
            description="Exercises managed controls.",
            worker_url="/worker.js",
            wasm_url="/demo.wasm",
        )

        content = render_to_string(
            "webapp/config_editor.jinja2",
            {"data_dict": {}, "headers": [], "plugin_editor_items": [editor_plugin]},
        )

        self.assertIn('data-editor-plugin="render-demo"', content)
        self.assertIn('data-plugin-action="run"', content)
        self.assertIn('data-plugin-action="reload"', content)
        self.assertIn('data-worker-url="/worker.js"', content)
        self.assertIn('data-wasm-url="/demo.wasm"', content)


class ExamplePluginTests(TestCase):
    def setUp(self) -> None:
        registry.reset()

    def tearDown(self) -> None:
        registry.reset()

    def test_manifest_registers_nav_only_ifc_editor_contribution(self) -> None:
        manifest = plugin_manifest()
        manifest.register(registry)

        nav_items = registry.get_active(NAV_ITEM_EXTENSION_POINT, enabled_ids={EXAMPLE_PLUGIN_ID})
        editor_plugins = registry.get_active(
            EDITOR_PLUGIN_EXTENSION_POINT,
            enabled_ids={EXAMPLE_PLUGIN_ID},
        )

        self.assertEqual(manifest.id, EXAMPLE_PLUGIN_ID)
        self.assertEqual(manifest.name, "Cadevil Rust IFC Editor")
        self.assertEqual(manifest.version, "2.0.0")
        self.assertEqual(len(nav_items), 1)
        self.assertEqual(nav_items[0].label, "IFC Editor")
        self.assertEqual(nav_items[0].url, "/plugins/ifc-editor/")
        self.assertEqual(editor_plugins, [])

    @override_settings(
        PLUGIN_BUILTINS={"cadevil.example.editor": "example_plugin:plugin_manifest"}
    )
    def test_bundled_plugin_is_discovered_without_installed_package_metadata(self) -> None:
        results = registry.discover_and_sync()

        result = next(item for item in results if item.plugin_id == EXAMPLE_PLUGIN_ID)
        self.assertTrue(result.ok)
        self.assertTrue(PluginRecord.objects.get(plugin_id=EXAMPLE_PLUGIN_ID).enabled)

    def test_example_browser_assets_are_available_to_staticfiles(self) -> None:
        for asset in (
            "js/plugins/example_plugin_worker.js",
            "js/ifc_editor_controller.js",
        ):
            with self.subTest(asset=asset):
                asset_path = finders.find(asset)
                self.assertIsNotNone(asset_path)
                self.assertGreater(Path(asset_path).stat().st_size, 8)

    def test_enabled_ifc_editor_plugin_is_rendered_in_the_spa_navigation(self) -> None:
        plugin_manifest().register(registry)
        PluginRecord.objects.create(
            plugin_id=EXAMPLE_PLUGIN_ID,
            name="Cadevil Rust IFC Editor",
            enabled=True,
        )
        user = get_user_model().objects.create_user(
            username="ifc-nav-user",
            password="pw12345678",
        )
        self.client.force_login(user)

        response = self.client.get("/")

        self.assertContains(response, "IFC Editor")
        self.assertContains(response, 'hx-get="/plugins/ifc-editor/"')
        self.assertContains(response, 'hx-target="#content-container"')


class RustExamplePluginTests(TestCase):
    def setUp(self) -> None:
        registry.reset()

    def tearDown(self) -> None:
        registry.reset()

    def test_manifest_registers_snake_navigation_without_editor_contribution(self) -> None:
        manifest = rust_plugin_manifest()
        manifest.register(registry)

        nav_items = registry.get_active(
            NAV_ITEM_EXTENSION_POINT,
            enabled_ids={RUST_EXAMPLE_PLUGIN_ID},
        )
        editor_plugins = registry.get_active(
            EDITOR_PLUGIN_EXTENSION_POINT,
            enabled_ids={RUST_EXAMPLE_PLUGIN_ID},
        )

        self.assertEqual(manifest.id, RUST_EXAMPLE_PLUGIN_ID)
        self.assertEqual(manifest.version, "2.0.0")
        self.assertEqual(len(nav_items), 1)
        self.assertEqual(nav_items[0].label, "Rust Snake")
        self.assertEqual(nav_items[0].url, "/plugins/rust-snake/")
        self.assertEqual(editor_plugins, [])

    @override_settings(
        PLUGIN_BUILTINS={
            "cadevil.rust-example.editor": "rust_example_plugin:plugin_manifest",
        }
    )
    def test_rust_plugin_is_discovered_as_a_bundled_plugin(self) -> None:
        results = registry.discover_and_sync()

        result = next(item for item in results if item.plugin_id == RUST_EXAMPLE_PLUGIN_ID)
        self.assertTrue(result.ok)
        self.assertTrue(PluginRecord.objects.get(plugin_id=RUST_EXAMPLE_PLUGIN_ID).enabled)

    def test_rust_source_and_snake_browser_assets_are_available(self) -> None:
        source_path = Path(__file__).parent.parent / "rust_example_plugin" / "src" / "lib.rs"
        source = source_path.read_text(encoding="utf-8")

        self.assertIn('pub extern "C" fn snake_reset', source)
        self.assertIn('pub extern "C" fn snake_tick', source)
        self.assertNotIn('fn triple', source)
        for asset in (
            "js/plugins/snake_game_controller.js",
            "js/plugins/snake_game_worker.js",
            "wasm/rust_example_plugin.wasm",
        ):
            with self.subTest(asset=asset):
                asset_path = finders.find(asset)
                self.assertIsNotNone(asset_path)
                self.assertGreater(Path(asset_path).stat().st_size, 8)

    def test_snake_page_is_an_authenticated_htmx_fragment(self) -> None:
        url = reverse("rust_example_plugin:snake_game")
        anonymous = self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertRedirects(
            anonymous,
            f"/accounts/login/?next={url}",
            fetch_redirect_response=False,
        )

        user = get_user_model().objects.create_user(
            username="snake-player",
            password="pw12345678",
        )
        self.client.force_login(user)
        direct = self.client.get(url)
        self.assertRedirects(direct, "/", fetch_redirect_response=False)

        response = self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.count(b'id="content-container"'), 1)
        self.assertContains(response, 'data-snake-game')
        self.assertContains(response, 'data-snake-canvas')
        self.assertContains(response, 'data-worker-url="/static/js/plugins/snake_game_worker.js"')
        self.assertContains(response, 'data-wasm-url="/static/wasm/rust_example_plugin.wasm"')
        self.assertContains(response, 'aria-live="polite"')
        self.assertNotContains(response, "<script")
        self.assertIn("HX-Request", response.headers["Vary"])

    def test_enabled_snake_plugin_is_rendered_in_the_spa_navigation(self) -> None:
        rust_plugin_manifest().register(registry)
        PluginRecord.objects.create(
            plugin_id=RUST_EXAMPLE_PLUGIN_ID,
            name="Rust Snake",
            enabled=True,
        )
        user = get_user_model().objects.create_user(
            username="snake-nav-player",
            password="pw12345678",
        )
        self.client.force_login(user)

        response = self.client.get("/")

        self.assertContains(response, "Rust Snake")
        self.assertContains(response, 'hx-get="/plugins/rust-snake/"')
        self.assertContains(response, 'hx-target="#content-container"')


class PluginRecordApiTests(TestCase):
    def setUp(self) -> None:
        self.record = PluginRecord.objects.create(plugin_id="api-plugin", name="Api Plugin", enabled=True)
        self.client = APIClient()

    def test_anonymous_user_is_rejected(self) -> None:
        response = self.client.get("/api/plugins/")
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_non_admin_user_is_forbidden(self) -> None:
        user = get_user_model().objects.create_user(username="regular", password="pw12345678")
        self.client.force_authenticate(user=user)
        response = self.client.get("/api/plugins/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_user_can_list_plugins(self) -> None:
        admin = get_user_model().objects.create_user(
            username="admin-api", password="pw12345678", is_staff=True
        )
        self.client.force_authenticate(user=admin)
        response = self.client.get("/api/plugins/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        plugin_ids = [item["plugin_id"] for item in response.data["results"]] if isinstance(response.data, dict) and "results" in response.data else [item["plugin_id"] for item in response.data]
        self.assertIn("api-plugin", plugin_ids)

    def test_admin_user_can_disable_and_enable_plugin(self) -> None:
        admin = get_user_model().objects.create_user(
            username="admin-toggle", password="pw12345678", is_staff=True
        )
        self.client.force_authenticate(user=admin)

        response = self.client.post(f"/api/plugins/{self.record.plugin_id}/disable/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.record.refresh_from_db()
        self.assertFalse(self.record.enabled)

        response = self.client.post(f"/api/plugins/{self.record.plugin_id}/enable/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.record.refresh_from_db()
        self.assertTrue(self.record.enabled)

    def test_plugin_with_discovery_error_cannot_be_enabled(self) -> None:
        self.record.enabled = False
        self.record.error = "Incompatible plugin API"
        self.record.save(update_fields=["enabled", "error"])
        admin = get_user_model().objects.create_user(
            username="admin-error", password="pw12345678", is_staff=True
        )
        self.client.force_authenticate(user=admin)

        response = self.client.post(f"/api/plugins/{self.record.plugin_id}/enable/")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.record.refresh_from_db()
        self.assertFalse(self.record.enabled)


class PluginManagementPageTests(TestCase):
    def setUp(self) -> None:
        self.record = PluginRecord.objects.create(plugin_id="page-plugin", name="Page Plugin", enabled=True)

    def test_anonymous_user_is_redirected_to_login(self) -> None:
        response = self.client.get(reverse("plugin_manager:plugin_list"))
        self.assertEqual(response.status_code, 302)

    def test_non_staff_user_is_forbidden(self) -> None:
        user = get_user_model().objects.create_user(username="viewer", password="pw12345678")
        self.client.force_login(user)
        response = self.client.get(reverse("plugin_manager:plugin_list"))
        self.assertEqual(response.status_code, 403)

    def test_staff_user_sees_plugin_list_fragment_via_htmx(self) -> None:
        staff = get_user_model().objects.create_user(
            username="staffer", password="pw12345678", is_staff=True
        )
        self.client.force_login(staff)
        response = self.client.get(
            reverse("plugin_manager:plugin_list"),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Page Plugin")
        self.assertContains(response, 'id="content-container"')
        self.assertNotContains(response, "<!DOCTYPE html>")
        self.assertIn("HX-Request", response.headers["Vary"])

    def test_plugin_list_uses_spa_theme_and_preserves_htmx_upload_target(self) -> None:
        staff = get_user_model().objects.create_user(
            username="staff-theme", password="pw12345678", is_staff=True
        )
        self.client.force_login(staff)

        response = self.client.get(
            reverse("plugin_manager:plugin_list"),
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, 'class="plugin-manager-shell"')
        self.assertContains(response, 'class="plugin-manager-hero"')
        self.assertContains(response, 'class="plugin-upload-card"')
        self.assertContains(response, 'class="plugin-table-viewport"')
        self.assertContains(response, 'class="plugin-table"')
        self.assertNotContains(response, 'class="flexgrid-container"')
        self.assertContains(response, 'hx-target="#plugin-table-body"')
        self.assertContains(response, 'hx-swap="beforeend"')
        self.assertContains(response, 'class="button plugin-upload-submit fa fa-upload"')

    def test_plugin_rows_render_accessible_status_and_error_states(self) -> None:
        self.record.enabled = False
        self.record.error = "Registration failed"
        self.record.save(update_fields=["enabled", "error"])
        staff = get_user_model().objects.create_user(
            username="staff-row-theme", password="pw12345678", is_staff=True
        )
        self.client.force_login(staff)

        response = self.client.get(
            reverse("plugin_manager:plugin_list"),
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, 'class="plugin-row plugin-row--error"')
        self.assertContains(response, 'class="plugin-status plugin-status--error"')
        self.assertContains(response, 'aria-live="polite"')
        self.assertContains(response, 'class="plugin-action-unavailable"')
        self.assertContains(response, "Registration failed")

    def test_staff_direct_request_redirects_to_application_shell(self) -> None:
        staff = get_user_model().objects.create_user(
            username="staff-direct", password="pw12345678", is_staff=True
        )
        self.client.force_login(staff)

        response = self.client.get(reverse("plugin_manager:plugin_list"))

        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_application_shell_loads_plugin_manager_with_htmx(self) -> None:
        staff = get_user_model().objects.create_user(
            username="staff-shell", password="pw12345678", is_staff=True
        )
        self.client.force_login(staff)

        response = self.client.get("/")

        self.assertContains(
            response,
            f'hx-get="{reverse("plugin_manager:plugin_list")}"',
        )
        self.assertContains(response, 'hx-target="#content-container"')
        self.assertContains(response, 'hx-swap="outerHTML"')
        self.assertContains(response, 'hx-push-url="true"')

    def test_plugin_lifecycle_is_managed_and_logged(self) -> None:
        self.record.enabled = False
        self.record.save(update_fields=["enabled"])

        with self.assertLogs("plugin_manager", level="INFO") as captured:
            loaded = manage_plugin(self.record.plugin_id, "load")
            unloaded = manage_plugin(self.record.plugin_id, "unload")

        self.assertTrue(loaded)
        self.assertFalse(unloaded)
        self.assertIn(f"Loading plugin '{self.record.plugin_id}'", captured.output[0])
        self.assertIn(f"Unloading plugin '{self.record.plugin_id}'", captured.output[1])

    def test_staff_user_can_toggle_plugin_via_htmx_endpoints(self) -> None:
        staff = get_user_model().objects.create_user(
            username="staff-toggle", password="pw12345678", is_staff=True
        )
        self.client.force_login(staff)

        response = self.client.post(
            reverse("plugin_manager:plugin_disable", args=[self.record.plugin_id])
        )
        self.assertEqual(response.status_code, 200)
        self.record.refresh_from_db()
        self.assertFalse(self.record.enabled)

        response = self.client.post(
            reverse("plugin_manager:plugin_enable", args=[self.record.plugin_id])
        )
        self.assertEqual(response.status_code, 200)
        self.record.refresh_from_db()
        self.assertTrue(self.record.enabled)

    def test_dotted_plugin_id_uses_relative_htmx_row_target(self) -> None:
        self.record.plugin_id = "cadevil.example.editor"
        self.record.save(update_fields=["plugin_id"])
        staff = get_user_model().objects.create_user(
            username="staff-target", password="pw12345678", is_staff=True
        )
        self.client.force_login(staff)

        response = self.client.get(
            reverse("plugin_manager:plugin_list"),
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, 'id="plugin-row-cadevil.example.editor"')
        self.assertContains(response, 'hx-target="closest tr"')
        self.assertNotContains(response, 'hx-target="#plugin-row-cadevil.example.editor"')

    def test_csrf_checked_htmx_flow_toggles_dotted_plugin_and_returns_row_fragment(self) -> None:
        self.record.plugin_id = "cadevil.example.editor"
        self.record.save(update_fields=["plugin_id"])
        staff = get_user_model().objects.create_user(
            username="staff-csrf", password="pw12345678", is_staff=True
        )
        client = Client(enforce_csrf_checks=True)
        client.force_login(staff)
        page_response = client.get("/")
        csrf_match = re.search(
            rb'"X-CSRFToken": "([^"]+)"',
            page_response.content,
        )

        self.assertEqual(page_response.status_code, 200)
        self.assertIsNotNone(csrf_match)
        csrf_token = csrf_match.group(1).decode()

        for route_name, expected_enabled in (
            ("plugin_disable", False),
            ("plugin_enable", True),
        ):
            with self.subTest(route_name=route_name):
                response = client.post(
                    reverse(f"plugin_manager:{route_name}", args=[self.record.plugin_id]),
                    HTTP_HX_REQUEST="true",
                    HTTP_X_CSRFTOKEN=csrf_token,
                )

                self.assertEqual(response.status_code, 200)
                self.assertContains(
                    response,
                    'id="plugin-row-cadevil.example.editor"',
                    html=False,
                )
                self.assertNotContains(response, 'id="content-container"')
                self.record.refresh_from_db()
                self.assertEqual(self.record.enabled, expected_enabled)

    def test_toggle_unknown_plugin_returns_not_found(self) -> None:
        staff = get_user_model().objects.create_user(
            username="staff-missing", password="pw12345678", is_staff=True
        )
        self.client.force_login(staff)

        response = self.client.post(
            reverse("plugin_manager:plugin_enable", args=["missing.plugin"]),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 404)

    def test_toggle_endpoints_reject_get_requests(self) -> None:
        staff = get_user_model().objects.create_user(
            username="staff-get", password="pw12345678", is_staff=True
        )
        self.client.force_login(staff)

        response = self.client.get(
            reverse("plugin_manager:plugin_disable", args=[self.record.plugin_id])
        )

        self.assertEqual(response.status_code, 405)
        self.record.refresh_from_db()
        self.assertTrue(self.record.enabled)

    def test_plugin_with_discovery_error_cannot_be_enabled_from_page(self) -> None:
        self.record.enabled = False
        self.record.error = "Registration failed"
        self.record.save(update_fields=["enabled", "error"])
        staff = get_user_model().objects.create_user(
            username="staff-error", password="pw12345678", is_staff=True
        )
        self.client.force_login(staff)

        response = self.client.post(
            reverse("plugin_manager:plugin_enable", args=[self.record.plugin_id])
        )

        self.assertEqual(response.status_code, 409)
        self.record.refresh_from_db()
        self.assertFalse(self.record.enabled)


@override_settings(PLUGIN_MAX_UPLOAD_SIZE=64)
class UploadedPluginTests(TestCase):
    def setUp(self) -> None:
        self.media_directory = TemporaryDirectory()
        self.override_media = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.override_media.enable()
        self.staff = get_user_model().objects.create_user(
            username="plugin-uploader",
            password="pw12345678",
            is_staff=True,
        )
        self.client.force_login(self.staff)

    def tearDown(self) -> None:
        self.override_media.disable()
        self.media_directory.cleanup()

    def _upload(self, filename: str, content: bytes, content_type: str):
        return self.client.post(
            reverse("plugin_manager:plugin_upload"),
            {
                "plugin_id": "uploaded.demo",
                "name": "Uploaded Demo",
                "artifact": SimpleUploadedFile(filename, content, content_type=content_type),
            },
            HTTP_HX_REQUEST="true",
        )

    def test_staff_can_upload_javascript_plugin_disabled_by_default(self) -> None:
        source = b"self.onmessage = () => postMessage({type: 'ready'});"

        response = self._upload("worker.js", source, "application/javascript")

        self.assertEqual(response.status_code, 201)
        record = PluginRecord.objects.get(plugin_id="uploaded.demo")
        self.assertEqual(record.source, PluginRecord.Source.UPLOAD)
        self.assertEqual(record.artifact_type, PluginRecord.ArtifactType.JAVASCRIPT)
        self.assertFalse(record.enabled)
        self.assertEqual(len(record.content_hash), 64)
        self.assertEqual(record.uploaded_by, self.staff)
        self.assertNotIn(source, response.content)

    def test_staff_can_upload_valid_webassembly_plugin(self) -> None:
        response = self._upload("calculation.wasm", b"\x00asm\x01\x00\x00\x00", "application/wasm")

        self.assertEqual(response.status_code, 201)
        record = PluginRecord.objects.get(plugin_id="uploaded.demo")
        self.assertEqual(record.artifact_type, PluginRecord.ArtifactType.WEBASSEMBLY)

    def test_upload_rejects_unsupported_or_malformed_artifacts(self) -> None:
        cases = (
            ("plugin.html", b"<script>alert(1)</script>", "text/html"),
            ("plugin.wasm", b"not wasm", "application/wasm"),
            ("plugin.js", b"\xff\xfe", "application/javascript"),
            ("plugin.js", b"x" * 65, "application/javascript"),
        )

        for filename, content, content_type in cases:
            with self.subTest(filename=filename, content=content):
                response = self._upload(filename, content, content_type)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response["HX-Retarget"], "#content-container")
                self.assertEqual(response["HX-Reswap"], "outerHTML")
                self.assertContains(response, 'id="content-container"', status_code=400)
                self.assertFalse(PluginRecord.objects.filter(plugin_id="uploaded.demo").exists())

    def test_non_staff_user_cannot_upload_plugin(self) -> None:
        user = get_user_model().objects.create_user(username="plugin-viewer", password="pw12345678")
        self.client.force_login(user)

        response = self._upload("worker.js", b"postMessage({type: 'ready'});", "application/javascript")

        self.assertEqual(response.status_code, 403)
        self.assertFalse(PluginRecord.objects.filter(plugin_id="uploaded.demo").exists())

    def test_uploaded_asset_is_protected_and_served_with_sandbox_headers(self) -> None:
        self._upload("worker.js", b"postMessage({type: 'ready'});", "application/javascript")
        manage_plugin("uploaded.demo", "load")

        response = self.client.get(
            reverse("plugin_manager:plugin_artifact", args=["uploaded.demo"])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/javascript")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Cross-Origin-Resource-Policy"], "same-origin")
        self.assertIn("connect-src 'none'", response["Content-Security-Policy"])

    def test_anonymous_user_cannot_download_uploaded_artifact(self) -> None:
        self._upload("worker.js", b"postMessage({type: 'ready'});", "application/javascript")
        manage_plugin("uploaded.demo", "load")
        self.client.logout()

        response = self.client.get(
            reverse("plugin_manager:plugin_artifact", args=["uploaded.demo"])
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_enabled_upload_is_exposed_as_declarative_editor_plugin(self) -> None:
        self._upload("plugin.wasm", b"\x00asm\x01\x00\x00\x00", "application/wasm")
        manage_plugin("uploaded.demo", "load")

        context = plugin_editor_items(RequestFactory().get("/config_editor/"))
        uploaded = next(item for item in context["plugin_editor_items"] if item.id == "uploaded.demo")

        self.assertEqual(uploaded.artifact_type, "wasm")
        self.assertIn("uploaded.demo", uploaded.wasm_url)
        self.assertTrue(uploaded.worker_url.endswith("wasm_plugin_worker.js"))


class PluginsManagementCommandTests(TestCase):
    def setUp(self) -> None:
        registry.reset()
        PluginRecord.objects.create(plugin_id="cmd-plugin", name="Cmd Plugin", enabled=True)

    def tearDown(self) -> None:
        registry.reset()

    def test_list_action_prints_plugins(self) -> None:
        out = StringIO()
        call_command("plugins", "list", stdout=out)
        self.assertIn("cmd-plugin", out.getvalue())

    def test_disable_and_enable_actions_update_record(self) -> None:
        out = StringIO()
        call_command("plugins", "disable", "cmd-plugin", stdout=out)
        self.assertFalse(PluginRecord.objects.get(plugin_id="cmd-plugin").enabled)

        call_command("plugins", "enable", "cmd-plugin", stdout=out)
        self.assertTrue(PluginRecord.objects.get(plugin_id="cmd-plugin").enabled)

    def test_enable_unknown_plugin_raises_command_error(self) -> None:
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("plugins", "enable", "does-not-exist")

    def test_enable_plugin_with_error_raises_command_error(self) -> None:
        from django.core.management.base import CommandError

        record = PluginRecord.objects.get(plugin_id="cmd-plugin")
        record.enabled = False
        record.error = "Broken hook"
        record.save(update_fields=["enabled", "error"])

        with self.assertRaises(CommandError):
            call_command("plugins", "enable", "cmd-plugin")

    def test_discover_action_runs_without_error(self) -> None:
        out = StringIO()
        call_command("plugins", "discover", stdout=out)
        self.assertIn("Discovered", out.getvalue())


class PluginReloadTests(TestCase):
    def setUp(self) -> None:
        registry.reset()
        self.staff = get_user_model().objects.create_user(
            username="reload-staff", password="pw12345678", is_staff=True
        )
        self.admin = get_user_model().objects.create_user(
            username="reload-admin", password="pw12345678", is_staff=True, is_superuser=True
        )

    def test_registry_reload_invalidates_caches_and_calls_discovery(self) -> None:
        with (
            unittest.mock.patch("plugin_manager.registry.importlib.invalidate_caches") as invalidate,
            unittest.mock.patch.object(registry, "discover_and_sync", return_value=[]) as discover,
        ):
            registry.reload()
        invalidate.assert_called_once_with()
        discover.assert_called_once_with()

    def test_reload_plugins_service_logs_partial_failure_summary(self) -> None:
        results = [
            DiscoveryResult(plugin_id="healthy"),
            DiscoveryResult(plugin_id="broken", error="Broken hook"),
        ]
        with self.assertLogs("plugin_manager", level="INFO") as captured:
            with unittest.mock.patch.object(registry, "reload", return_value=results):
                self.assertEqual(reload_plugins(), results)
        self.assertIn("Reloading plugins", captured.output[0])
        self.assertIn("1 loaded, 1 failed", captured.output[-1])

    def test_reload_preserves_enabled_state_where_appropriate(self) -> None:
        PluginRecord.objects.create(plugin_id="keep-me", name="Keep Me", enabled=True)

        def loader():
            return _make_manifest("keep-me")

        entry_points = [_FakeEntryPoint("keep-me", loader)]
        with unittest.mock.patch("plugin_manager.registry.entry_points", return_value=entry_points):
            reload_plugins()

        record = PluginRecord.objects.get(plugin_id="keep-me")
        self.assertTrue(record.enabled)

    def test_staff_user_can_reload_plugins_via_htmx(self) -> None:
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("plugin_manager:plugin_reload"),
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Plugin Manager")
        self.assertContains(response, 'id="content-container"')
        self.assertContains(response, "Reload complete:")
        self.assertContains(response, 'role="status"')
        self.assertContains(response, 'id="plugin-navigation-items"')
        self.assertContains(response, 'hx-swap-oob="outerHTML"')

    def test_reload_hot_loads_navigation_into_the_existing_spa_shell(self) -> None:
        registry.register_nav_item(
            "hot-nav",
            NavItem(label="Hot loaded tool", url="/hot-tool/", icon="fa-bolt"),
        )
        PluginRecord.objects.create(plugin_id="hot-nav", name="Hot Nav", enabled=True)
        results = [DiscoveryResult(plugin_id="hot-nav", name="Hot Nav")]
        self.client.force_login(self.staff)

        with unittest.mock.patch("plugin_manager.views.reload_plugins", return_value=results):
            response = self.client.post(
                reverse("plugin_manager:plugin_reload"),
                HTTP_HX_REQUEST="true",
            )

        self.assertContains(response, "Hot loaded tool")
        self.assertContains(response, 'hx-get="/hot-tool/"')
        self.assertContains(response, 'hx-swap-oob="outerHTML"')

    def test_reload_htmx_requires_staff(self) -> None:
        user = get_user_model().objects.create_user(username="no-staff", password="pw12345678")
        self.client.force_login(user)
        response = self.client.post(reverse("plugin_manager:plugin_reload"))
        self.assertEqual(response.status_code, 403)

    def test_reload_htmx_rejects_get(self) -> None:
        self.client.force_login(self.staff)
        response = self.client.get(reverse("plugin_manager:plugin_reload"))
        self.assertEqual(response.status_code, 405)

    def test_reload_post_without_htmx_redirects_to_the_shell(self) -> None:
        self.client.force_login(self.staff)
        response = self.client.post(reverse("plugin_manager:plugin_reload"))
        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_admin_user_can_reload_plugins_via_api(self) -> None:
        client = APIClient()
        client.force_authenticate(user=self.admin)
        response = client.post("/api/plugins/reload/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], response.data["loaded"] + response.data["failed"])
        self.assertIsInstance(response.data["errors"], list)

    def test_regular_user_cannot_reload_plugins_via_api(self) -> None:
        user = get_user_model().objects.create_user(
            username="reload-api-user",
            password="pw12345678",
        )
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.post("/api/plugins/reload/")
        self.assertEqual(response.status_code, 403)

    def test_reload_command_output(self) -> None:
        out = StringIO()
        call_command("plugins", "reload", stdout=out)
        self.assertIn("Reloading plugins...", out.getvalue())
        self.assertIn("Discovered", out.getvalue())
