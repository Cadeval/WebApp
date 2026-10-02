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

from apps.plugins.example_plugin import EXAMPLE_PLUGIN_ID, plugin_manifest
from apps.plugin_manager.context_processors import plugin_editor_items, plugin_nav_items
from apps.plugin_manager.manifest import (
    PluginError,
    PLUGIN_API_VERSION,
    PluginManifest,
    is_api_version_compatible,
)
from apps.plugin_manager.models import PluginRecord, UserPluginSelection
from apps.plugin_manager.registry import (
    EDITOR_PLUGIN_EXTENSION_POINT,
    NAV_ITEM_EXTENSION_POINT,
    DiscoveryResult,
    EditorPlugin,
    NavItem,
    registry,
)
from apps.plugin_manager.services import manage_plugin, reload_plugins
from apps.plugins.rust_example_plugin import RUST_EXAMPLE_PLUGIN_ID
from apps.plugins.rust_example_plugin import plugin_manifest as rust_plugin_manifest


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

        active = registry.get_active(NAV_ITEM_EXTENSION_POINT, enabled_ids={"high-priority", "low-priority"})

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



from django_bolt import BoltAPI
from tests.bolt_browser import BoltBrowser
from config.api import api as main_api
from apps.mycelium.api import api as home_api
from apps.plugin_manager.api import api as manager_api
from apps.plugin_manager.forms import PluginUploadForm
from apps.plugin_manager.services import create_uploaded_plugin
from apps.plugin_manager.registry import PluginRegistry
from django.db import IntegrityError
from unittest.mock import patch
import json


class HardenedRegistryTests(TestCase):
    def setUp(self):
        self.registry = PluginRegistry()

    def test_failed_hook_rolls_back_all_contributions_and_manifest(self):
        def fail(reg):
            reg.register_nav_item("broken", NavItem("Partial", "/partial"))
            raise RuntimeError("failed")
        results = self.registry.discover_plugins([_FakeEntryPoint("broken", lambda: _make_manifest("broken", fail))])
        self.assertFalse(results[0].ok)
        self.assertEqual(self.registry.manifests(), {})
        self.assertEqual(self.registry.get_active(NAV_ITEM_EXTENSION_POINT, ["broken"]), [])

    def test_hook_cannot_register_for_another_plugin(self):
        def forge(reg): reg.register_nav_item("other", NavItem("Forged", "/forged"))
        results = self.registry.discover_plugins([_FakeEntryPoint("bad", lambda: _make_manifest("bad", forge))])
        self.assertFalse(results[0].ok)
        self.assertEqual(self.registry.get_extensions(NAV_ITEM_EXTENSION_POINT), [])

    def test_error_records_are_inactive_even_after_direct_enable(self):
        PluginRecord.objects.create(plugin_id="bad", enabled=True, error="failed")
        self.assertNotIn("bad", self.registry._enabled_plugin_ids())

    def test_stale_record_cannot_reenable_a_failed_plugin(self):
        from apps.plugin_manager.models import PluginActivationError
        stale=PluginRecord.objects.create(plugin_id="stale",enabled=False)
        PluginRecord.objects.filter(pk=stale.pk).update(error="new discovery failure")
        with self.assertRaises(PluginActivationError): stale.set_enabled(True)
        stale.refresh_from_db(); self.assertFalse(stale.enabled)

    def test_removed_package_is_disabled_but_uploaded_state_is_preserved(self):
        record = PluginRecord.objects.create(plugin_id="removed", enabled=True)
        upload = PluginRecord.objects.create(plugin_id="upload", source="upload", enabled=True)
        self.registry.sync_plugin_records([])
        record.refresh_from_db(); upload.refresh_from_db()
        self.assertFalse(record.enabled); self.assertTrue(record.error); self.assertTrue(upload.enabled)

    def test_package_cannot_overwrite_uploaded_record(self):
        record = PluginRecord.objects.create(plugin_id="collision", source="upload", name="User upload", enabled=False)
        self.registry.discover_and_sync([_FakeEntryPoint("collision", lambda: _make_manifest("collision"))])
        record.refresh_from_db()
        self.assertEqual(record.name, "User upload")
        self.assertFalse(record.enabled)
        self.assertEqual(self.registry.manifests(), {})

    def test_collision_cannot_reactivate_after_other_worker_refresh(self):
        record=PluginRecord.objects.create(plugin_id="collision",source="upload",enabled=True)
        def contribute(reg): reg.register_nav_item("collision",NavItem("Forged package", "/forged"))
        point=_FakeEntryPoint("collision",lambda:_make_manifest("collision",contribute))
        self.registry.discover_and_sync([point])
        PluginRecord.objects.create(plugin_id="changed")
        with patch("apps.plugin_manager.registry.entry_points",return_value=[point]):
            self.registry.refresh_if_changed()
        self.assertNotIn("collision",self.registry._enabled_plugin_ids())
        self.assertEqual(self.registry.get_active(NAV_ITEM_EXTENSION_POINT),[])

    def test_other_worker_refreshes_metadata_without_writing_toggle_state(self):
        self.registry._record_signature = ()
        record = PluginRecord.objects.create(plugin_id="new", enabled=False)
        with patch.object(self.registry, "discover_plugins") as discover:
            self.registry.refresh_if_changed(); self.registry.refresh_if_changed()
        discover.assert_called_once()
        record.refresh_from_db(); self.assertFalse(record.enabled)

    def test_invalid_contribution_urls_are_rejected(self):
        for url in ["javascript:alert(1)", "//outside.test/x", "/../secret", "/x\\y", "/x\n"]:
            with self.subTest(url=url), self.assertRaises(PluginError):
                self.registry.register_nav_item("test", NavItem("Bad", url))

    def test_malformed_manifests_are_isolated_with_safe_rejection_ids(self):
        for identifier in [None,[],"../outside", "x"*256]:
            with self.subTest(identifier=identifier):
                results=self.registry.discover_and_sync([_FakeEntryPoint("invalid",lambda:PluginManifest(id=identifier))])
                self.assertFalse(results[0].ok)
                self.assertIsInstance(results[0].plugin_id,str)
        results=self.registry.discover_plugins([_FakeEntryPoint("bad",lambda:PluginManifest(id="bad",priority="bad"))])
        self.assertFalse(results[0].ok)

    def test_huge_manifest_priority_does_not_abort_healthy_plugin_sync(self):
        results=self.registry.discover_and_sync([
            _FakeEntryPoint("huge",lambda:PluginManifest(id="huge",priority=1<<100)),
            _FakeEntryPoint("healthy",lambda:PluginManifest(id="healthy"))])
        self.assertFalse(results[0].ok); self.assertTrue(results[1].ok)
        self.assertTrue(PluginRecord.objects.get(plugin_id="healthy").enabled)

    def test_invalid_priority_cannot_break_valid_navigation(self):
        with self.assertRaises(PluginError):
            self.registry.register_nav_item("bad",NavItem("Bad","/bad",priority="bad"))
        self.registry.register_nav_item("good",NavItem("Good","/good",priority=1))
        self.assertEqual([item.label for item in self.registry.get_active(NAV_ITEM_EXTENSION_POINT,["good"])],["Good"])

    def test_upload_storage_is_removed_when_database_insert_fails(self):
        with TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=Path(folder)/"media", PLUGIN_ARTIFACT_ROOT=Path(folder)/"private"):
            from .package_fixtures import signed_package
            form=PluginUploadForm({}, {"artifact":SimpleUploadedFile("package.zip", signed_package(plugin_id="storage-failure"), content_type="application/zip")})
            self.assertTrue(form.is_valid())
            with patch.object(PluginRecord, "save", side_effect=IntegrityError("duplicate")), self.assertRaises(IntegrityError):
                create_uploaded_plugin(form, None)
            self.assertEqual([p for p in Path(folder).rglob("*") if p.is_file()], [])


@override_settings(STATIC_URL="/static/")
class NativePluginTests(TestCase):
    def setUp(self):
        self.folder=TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.settings_override=override_settings(MEDIA_ROOT=str(Path(self.folder.name)/"media"), PLUGIN_ARTIFACT_ROOT=str((Path(self.folder.name)/"private").resolve()))
        self.settings_override.enable(); self.addCleanup(self.settings_override.disable)
        combined=BoltAPI(trailing_slash="keep", django_middleware=True)
        for child in [main_api, home_api, manager_api]: combined.mount("", child)
        self.client=BoltBrowser(api=combined); self.addCleanup(self.client.close)
        self.staff=get_user_model().objects.create_user(username="plugin-staff", is_staff=True)
        self.regular=get_user_model().objects.create_user(username="plugin-user")
        self.record=PluginRecord.objects.create(plugin_id="native-test", name="Native test", enabled=False)
        self.client.force_login(self.staff)
        self.client.get("/plugins/manage/")

    def action(self, action): return f"/plugins/{self.record.plugin_id}/{action}/"
    def upload(self, content=None, name="package.zip", content_type="application/zip", plugin_id="uploaded-test", *, wasm=False):
        from .package_fixtures import signed_package
        if content is None:
            content=signed_package(self.staff, plugin_id, wasm=wasm)
        return self.client.post("/plugins/upload/", {"artifact":SimpleUploadedFile(name,content,content_type=content_type)}, HTTP_HX_REQUEST="true")

    def test_manager_full_and_fragment_use_one_content_boundary(self):
        full=self.client.get("/plugins/manage/")
        self.assertContains(full,"<html"); self.assertContains(full,'id="content-container"',count=1)
        fragment=self.client.get("/plugins/manage/",HTTP_HX_REQUEST="true")
        self.assertNotContains(fragment,"<html"); self.assertContains(fragment,'id="content-container"',count=1)

    def test_anonymous_redirects_and_regular_users_only_browse_or_publish(self):
        self.client.logout(); self.assertEqual(self.client.get("/plugins/manage/").status_code,302)
        self.client.force_login(self.regular)
        self.assertEqual(self.client.get("/plugins/manage/").status_code,200)
        for path in [self.action("enable"),self.action("disable"),"/plugins/reload/"]:
            self.assertEqual(self.client.post(path).status_code,403)
        self.assertEqual(self.client.post("/plugins/upload/").status_code,400)

    def test_mutations_are_post_only_and_csrf_protected(self):
        for path in [self.action("enable"),self.action("disable"),"/plugins/reload/","/plugins/upload/"]:
            self.assertIn(self.client.get(path).status_code,(404,405))
            self.client.auto_csrf=False
            self.assertEqual(self.client.post(path).status_code,403)
        self.record.refresh_from_db(); self.assertFalse(self.record.enabled)

    def test_enable_disable_preserve_state_and_refresh_navigation(self):
        for action, expected in [("enable",True),("disable",False)]:
            response=self.client.post(self.action(action),HTTP_HX_REQUEST="true")
            self.assertEqual(response.status_code,200)
            self.assertContains(response,'hx-swap-oob="outerHTML"')
            self.record.refresh_from_db(); self.assertEqual(self.record.enabled,expected)

    def test_error_activation_returns_conflict(self):
        self.record.error="incompatible"; self.record.save()
        self.assertEqual(self.client.post(self.action("enable")).status_code,409)
        self.record.refresh_from_db(); self.assertFalse(self.record.enabled)

    def test_unknown_plugin_returns_not_found(self):
        self.assertEqual(self.client.post("/plugins/unknown/enable/").status_code,404)

    def test_upload_starts_disabled_and_requires_enabled_artifact(self):
        response=self.upload(); self.assertEqual(response.status_code,201)
        record=PluginRecord.objects.get(plugin_id="uploaded-test"); self.assertFalse(record.enabled)
        path="/plugins/uploaded-test/assets/worker.js"
        self.assertEqual(self.client.get(path).status_code,404)
        self.assertEqual(self.client.post("/plugins/uploaded-test/enable/").status_code,200)
        self.assertEqual(self.client.get(path).status_code,404)
        self.assertEqual(self.client.post("/plugins/store/uploaded-test/enable/",HTTP_HX_REQUEST="true").status_code,200)
        response=self.client.get(path); self.assertEqual(response.status_code,200)
        self.assertEqual(response['X-Content-Type-Options'],'nosniff')
        self.assertEqual(response['Cache-Control'],'private, no-store')
        self.assertIn("connect-src 'none'",response['Content-Security-Policy'])
        self.client.logout(); self.client.force_login(self.regular); self.assertEqual(self.client.get(path).status_code,404)
        self.assertEqual(self.client.post("/plugins/store/uploaded-test/enable/",HTTP_HX_REQUEST="true").status_code,200)
        self.assertEqual(self.client.get(path).status_code,200)
        self.client.logout(); self.assertEqual(self.client.get(path).status_code,302)

    def test_plugin_upload_is_outside_public_media_storage(self):
        self.upload()
        record=PluginRecord.objects.get(plugin_id="uploaded-test")
        self.assertFalse(Path(record.artifact.path).is_relative_to((Path(self.folder.name)/"media").resolve()))
        self.assertTrue(Path(record.artifact.path).is_relative_to((Path(self.folder.name)/"private").resolve()))

    def test_disabled_or_error_upload_artifact_is_unavailable(self):
        self.upload(); record=PluginRecord.objects.get(plugin_id="uploaded-test")
        UserPluginSelection.objects.create(user=self.staff,plugin=record)
        record.enabled=True; record.error="failed"; record.save()
        self.assertEqual(self.client.get("/plugins/uploaded-test/assets/worker.js").status_code,404)

    def test_missing_uploaded_file_returns_not_found(self):
        self.upload(); record=PluginRecord.objects.get(plugin_id="uploaded-test")
        UserPluginSelection.objects.create(user=self.staff,plugin=record)
        record.enabled=True; record.save(); record.artifact.delete(save=False)
        self.assertEqual(self.client.get("/plugins/uploaded-test/assets/worker.js").status_code,404)

    def test_invalid_or_empty_artifacts_are_rejected(self):
        for content,name,mime in [(b"", "empty.js", "text/javascript"), (b"<script>x</script>","html.js","text/javascript"), (b"\x00asm", "truncated.wasm", "application/wasm"), (b"\x00asm\x02\x00\x00\x00", "version.wasm", "application/wasm"), (b"print(1)","server.py","text/plain")]:
            with self.subTest(name=name): self.assertEqual(self.upload(content,name,mime).status_code,400)
        self.assertFalse(PluginRecord.objects.filter(source="upload").exists())

    def test_upload_wasm_header_and_editor_urls(self):
        self.assertEqual(self.upload(wasm=True).status_code,201)
        record=PluginRecord.objects.get(plugin_id="uploaded-test"); record.set_enabled(True)
        UserPluginSelection.objects.create(user=self.staff,plugin=record)
        request=RequestFactory().get("/");request.user=self.staff
        items=plugin_editor_items(request)["plugin_editor_items"]
        item=next(item for item in items if item.id==record.plugin_id)
        self.assertEqual(item.worker_url,"/static/js/plugins/wasm_plugin_worker.js?v=20261002-plugins")
        self.assertEqual(item.wasm_url,"/plugins/uploaded-test/assets/calculator.wasm")

    def test_native_configuration_editor_contains_upload_controls(self):
        self.upload()
        record=PluginRecord.objects.get(plugin_id="uploaded-test");record.set_enabled(True)
        UserPluginSelection.objects.create(user=self.staff,plugin=record)
        from apps.plugin_manager.context_processors import _uploaded_editor_items
        html=render_to_string("bim/editor.html",{"plugin_editor_items":_uploaded_editor_items(self.staff),"fragment":True})
        self.assertIn('data-editor-plugin="uploaded-test"',html)
        self.assertIn('data-worker-url="/plugins/uploaded-test/assets/worker.js"',html)
        self.assertIn('data-plugin-action="run"',html)

    def test_duplicate_upload_is_readable_validation_error(self):
        self.assertEqual(self.upload().status_code,201)
        self.assertEqual(self.upload().status_code,400)
        self.assertEqual(PluginRecord.objects.filter(plugin_id="uploaded-test").count(),1)

    def test_refresh_preserves_disabled_upload(self):
        self.upload()
        response=self.client.post("/plugins/reload/",HTTP_HX_REQUEST="true")
        self.assertContains(response,"Discovery refreshed")
        self.assertFalse(PluginRecord.objects.get(plugin_id="uploaded-test").enabled)

    def test_browser_pages_require_active_package_and_authentication(self):
        for plugin_id,path,title in [(EXAMPLE_PLUGIN_ID,"/plugins/ifc-editor/","IFC Editor"),(RUST_EXAMPLE_PLUGIN_ID,"/plugins/rust-snake/","Snake")]:
            record,_=PluginRecord.objects.update_or_create(plugin_id=plugin_id,defaults={"enabled":True,"error":""})
            UserPluginSelection.objects.get_or_create(user=self.staff,plugin=record)
            response=self.client.get(path,HTTP_HX_REQUEST="true"); self.assertContains(response,title)
            self.assertContains(response,'id="content-container"',count=1)
            self.assertNotContains(response,"<html")
            self.assertContains(self.client.get(path),"<html")
            record.set_enabled(False); self.assertEqual(self.client.get(path).status_code,404)
            record.enabled=True; record.error="failed"; record.save(); self.assertEqual(self.client.get(path).status_code,404)
        self.client.logout(); self.assertEqual(self.client.get("/plugins/rust-snake/").status_code,302)

    def test_bim_error_gate_blocks_enabled_record_with_error(self):
        record,_=PluginRecord.objects.update_or_create(plugin_id="cadevil.bim.model_manager",defaults={"enabled":True,"error":"failed"})
        UserPluginSelection.objects.create(user=self.staff,plugin=record)
        self.assertEqual(self.client.get("/plugins/bim/model_manager/").status_code,404)

    def test_full_page_navigation_remains_regular_link(self):
        item=NavItem("Full", "/full",full_page=True)
        html=render_to_string("plugin_manager/_plugin_nav_items.jinja2", {"plugin_nav_items":[item]})
        self.assertIn('href="/full"',html); self.assertNotIn('hx-get',html)
