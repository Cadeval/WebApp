"""Plugin-owned Django apps and resources use the normal registry/loaders."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

from django.apps import apps
from django.apps.registry import Apps
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.db.migrations.loader import MigrationLoader
from django.forms.renderers import get_default_renderer
from django.template.loader import get_template
from django.templatetags.static import static
from django.test import SimpleTestCase, TestCase, override_settings

from apps.plugins.bim_model_manager.model_choice_widgets import (
    ModelThumbnailCheckboxSelect,
    ModelThumbnailRadioSelect,
)
from tests.bolt_browser import BoltBrowser

from .models import PluginRecord, UserPluginSelection
from .django_resources import PluginResourceConfig
from .resource_registry import ResourceBundle, ResourceRegistry


ROOT = Path(__file__).resolve().parents[2]
BIM_ROOT = ROOT / "apps/plugins/bim_model_manager"
EDITOR_ROOT = ROOT / "apps/plugins/example_plugin"


@override_settings(STATIC_URL="/static/")
class DjangoPluginResourceTests(SimpleTestCase):
    def test_django_template_loader_resolves_plugin_owned_origins(self):
        for root, names in (
            (BIM_ROOT, ("bim/models.html", "bim/demo.html", "bim/viewer.html",
                        "shared/material_passport_report.html",
                        "shared/assessment_diagnostics.html")),
            (EDITOR_ROOT, ("example_plugin/ifc_editor.jinja2",)),
        ):
            for name in names:
                with self.subTest(template=name):
                    self.assertEqual(Path(get_template(name).origin.name), root / "templates" / name)
        self.assertEqual(Path(get_template("shared/page.html").origin.name),
                         ROOT / "apps/shared/templates/shared/page.html")

    def test_default_django_form_renderer_discovers_plugin_widgets(self):
        renderer = get_default_renderer()
        for widget, value, input_type in (
            (ModelThumbnailRadioSelect(choices=[("owned", "Owned model")]), "owned", "radio"),
            (ModelThumbnailCheckboxSelect(choices=[("owned", "Owned model")]), ["owned"], "checkbox"),
        ):
            with self.subTest(widget=widget.__class__.__name__):
                template = renderer.get_template(widget.template_name)
                self.assertEqual(Path(template.origin.name), BIM_ROOT / "templates" / widget.template_name)
                markup = widget.render("model", value)
                self.assertIn(f'type="{input_type}"', markup)
                self.assertIn('value="owned"', markup)
                self.assertIn("checked", markup)
                self.assertIn("Owned model", markup)

    def test_plugin_persistence_and_resource_only_adapter_enter_the_registry(self):
        configs = {config.name: config for config in apps.get_app_configs()
                   if config.name.startswith("apps.plugins.")}
        self.assertEqual(set(configs), {
            "apps.plugins.bim_model_manager.django",
            "apps.plugins.example_plugin.resources",
        })
        bim = configs["apps.plugins.bim_model_manager.django"]
        editor = configs["apps.plugins.example_plugin.resources"]
        self.assertEqual(bim.label, "bim_model_manager")
        self.assertEqual(Path(bim.path), BIM_ROOT)
        self.assertEqual(len(list(bim.get_models())), 9)
        self.assertEqual(apps.get_model("bim_model_manager", "FileUpload").__module__,
                         "apps.plugins.bim_model_manager.django.models")
        self.assertIsNone(editor.models_module)
        self.assertEqual(list(editor.get_models()), [])
        loader = MigrationLoader(None, ignore_no_migrations=True)
        self.assertIn(bim.label, loader.migrated_apps)
        self.assertNotIn(editor.label, loader.migrated_apps)
        self.assertNotIn("FileUpload", {model.__name__ for model in apps.get_app_config("shared").get_models()})

    def test_static_finders_preserve_public_urls_and_resolve_only_plugin_roots(self):
        for root, relative in (
            (BIM_ROOT, "css/bim.css"),
            (BIM_ROOT, "js/3d_view.js"),
            (BIM_ROOT, "bim-demo/demo-recording.json"),
            (EDITOR_ROOT, "css/ifc_editor.css"),
            (EDITOR_ROOT, "js/ifc_editor_controller.js"),
            (EDITOR_ROOT, "js/plugins/example_plugin_worker.js"),
            (EDITOR_ROOT, "wasm/example_plugin.wasm"),
        ):
            with self.subTest(asset=relative):
                expected = root / "static" / relative
                self.assertEqual(Path(finders.find(relative)), expected)
                self.assertEqual({Path(path) for path in finders.find(relative, find_all=True)}, {expected})
                self.assertEqual(static(relative), "/static/" + relative)
        self.assertIsNone(finders.find("resources.json"))
        self.assertIsNone(finders.find("models.py"))

    def test_resource_adapter_ignores_later_child_models_and_migrations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            namespace = root / "resources"
            (namespace / "migrations").mkdir(parents=True)
            (namespace / "models.py").write_text(
                'raise AssertionError("Resource child models must not execute")\n')
            (namespace / "migrations/__init__.py").write_text(
                'raise AssertionError("Resource child migrations must not execute")\n')
            config = PluginResourceConfig(ResourceBundle(
                "cadevil.example.editor", "apps.plugins.example_plugin", root))
            # Simulate a future source file inside the namespace without
            # writing into the real plugin or changing the global app registry.
            with patch.object(config.module, "__path__", [str(namespace)]), \
                    override_settings(STATICFILES_DIRS=[], MIGRATION_MODULES={}):
                isolated_apps = Apps([config])
                self.assertIsNone(config.models_module)
                self.assertEqual(list(isolated_apps.get_models()), [])
                with patch("django.db.migrations.loader.apps", isolated_apps):
                    loader = MigrationLoader(None)
                self.assertEqual(loader.disk_migrations, {})
                self.assertNotIn(config.label, loader.migrated_apps)
                imported = [name for name in (config.name + ".models", config.name + ".migrations")
                            if name in sys.modules]
                self.assertEqual(imported, [])


class ResourceMetadataTests(SimpleTestCase):
    def fixture(self, project_root, module="fixture", plugin_id="cadevil.fixture"):
        root = project_root / "apps/plugins" / module
        (root / "resources").mkdir(parents=True)
        (root / "resources/__init__.py").write_text('"""Model-free resource namespace."""\n')
        (root / "templates").mkdir()
        (root / "static").mkdir()
        metadata = {"version": 1, "plugin_id": plugin_id, "package": "apps.plugins." + module}
        (root / "resources.json").write_text(json.dumps(metadata))
        return root, metadata

    def test_neutral_discovery_imports_neither_django_nor_plugin_factories(self):
        script = """import json, sys
from pathlib import Path
from apps.plugin_manager.resource_registry import ResourceRegistry
registry = ResourceRegistry.from_builtins(Path.cwd(), {
 'cadevil.bim.model_manager': 'bim_model_manager:plugin_manifest',
 'cadevil.example.editor': 'example_plugin:plugin_manifest',
})
print(json.dumps({'owners': [item.plugin_id for item in registry.bundles()],
 'django_imported': any(name == 'django' or name.startswith('django.') for name in sys.modules),
 'plugin_imported': any(name.startswith('apps.plugins.') for name in sys.modules)}))
"""
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                                check=True, capture_output=True, text=True, timeout=10)
        observed = json.loads(result.stdout)
        self.assertEqual(set(observed["owners"]), {"cadevil.bim.model_manager", "cadevil.example.editor"})
        self.assertFalse(observed["django_imported"])
        self.assertFalse(observed["plugin_imported"])

    def test_settings_resource_hooks_preserve_base_and_filter_production_tools(self):
        script = """import json, os, runpy
from unittest.mock import patch
os.environ['SECRET_KEY'] = 'resource-settings-tests-only-0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
from config.settings import base
from apps.plugin_manager.django_resources import resource_app_configs
baseline = tuple(base.INSTALLED_APPS)
with patch('apps.plugin_manager.django_resources.resource_app_configs', wraps=resource_app_configs) as hook:
 from config.settings import dev, prod
 repeated = runpy.run_module('config.settings.prod')
 calls = [list(call.args[1]) for call in hook.call_args_list]
names = lambda entries: [entry.name for entry in entries if not isinstance(entry, str)]
print(json.dumps({'base_unchanged': tuple(base.INSTALLED_APPS) == baseline,
 'dev_names': names(dev.INSTALLED_APPS), 'prod_names': names(prod.INSTALLED_APPS),
 'repeated_names': names(repeated['INSTALLED_APPS']),
 'dev_hook_has_mcp': any(owner.startswith('cadevil.mcp.') for owner in calls[0]),
 'prod_hooks_have_mcp': any(owner.startswith('cadevil.mcp.') for owners in calls[1:] for owner in owners)}))
"""
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                                check=True, capture_output=True, text=True, timeout=10)
        observed = json.loads(result.stdout)
        expected = {"apps.plugins.bim_model_manager.django", "apps.plugins.example_plugin.resources"}
        self.assertTrue(observed["base_unchanged"])
        for key in ("dev_names", "prod_names", "repeated_names"):
            self.assertEqual(set(observed[key]), expected)
            self.assertEqual(len(observed[key]), len(expected))
        self.assertTrue(observed["dev_hook_has_mcp"])
        self.assertFalse(observed["prod_hooks_have_mcp"])

    def test_only_explicit_configured_source_packages_are_discovered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            self.fixture(root, module="unconfigured", plugin_id="cadevil.unconfigured")
            registry = ResourceRegistry.from_builtins(root, {"cadevil.fixture": "fixture:manifest"})
            self.assertEqual([bundle.plugin_id for bundle in registry.bundles()], ["cadevil.fixture"])
            self.assertEqual(ResourceRegistry.from_builtins(root, {}).bundles(), ())

    def test_invalid_version_package_and_unknown_metadata_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package, metadata = self.fixture(root)
            for changes in ({"version": True}, {"version": 2},
                            {"package": "apps.plugins.other"}, {"extra": "not allowed"},
                            {"plugin_id": "../outside"}, {"plugin_id": None}):
                with self.subTest(changes=changes):
                    (package / "resources.json").write_text(json.dumps({**metadata, **changes}))
                    with self.assertRaises(ValueError):
                        ResourceRegistry.from_builtins(root, {"cadevil.fixture": "fixture:manifest"})

    def test_oversized_declaration_and_missing_adapter_namespace_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package, metadata = self.fixture(root)
            (package / "resources.json").write_text(" " * 4097)
            with self.assertRaisesRegex(ValueError, "too large"):
                ResourceRegistry.from_builtins(root, {"cadevil.fixture": "fixture:manifest"})
            (package / "resources.json").write_text(json.dumps(metadata))
            (package / "resources/__init__.py").unlink()
            with self.assertRaisesRegex(ValueError, "model-free"):
                ResourceRegistry.from_builtins(root, {"cadevil.fixture": "fixture:manifest"})

    def test_duplicate_owner_and_package_declarations_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            self.fixture(root, module="other")
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                ResourceRegistry.from_builtins(root, {"first": "fixture:manifest", "second": "other:manifest"})
            registry = ResourceRegistry()
            registry.register(ResourceBundle("cadevil.first", "apps.plugins.fixture", root))
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                registry.register(ResourceBundle("cadevil.second", "apps.plugins.fixture", root))

    def test_symlink_metadata_resources_and_initializer_are_rejected(self):
        for relative in ("resources.json", "templates", "static", "resources", "resources/__init__.py"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                package, _ = self.fixture(root)
                original = package / relative
                outside = root / "outside"
                original.rename(outside)
                original.symlink_to(outside, target_is_directory=outside.is_dir())
                with self.assertRaises(ValueError):
                    ResourceRegistry.from_builtins(root, {"cadevil.fixture": "fixture:manifest"})

    def test_symlink_package_ancestors_cannot_alias_other_source_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package, _ = self.fixture(root, module="nested/plugin")
            declaration = package / "resources.json"
            metadata = json.loads(declaration.read_text())
            metadata["package"] = "apps.plugins.nested.plugin"
            declaration.write_text(json.dumps(metadata))
            ancestor = package.parent
            target = ancestor.with_name("other")
            ancestor.rename(target)
            ancestor.symlink_to(target, target_is_directory=True)
            with self.assertRaises(ValueError):
                ResourceRegistry.from_builtins(root, {"cadevil.fixture": "nested.plugin:manifest"})


@override_settings(STATIC_URL="/static/")
class PluginResourceActivationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="resource-owner")
        self.plugin = PluginRecord.objects.create(plugin_id="cadevil.example.editor", enabled=True)
        self.browser = BoltBrowser()
        self.addCleanup(self.browser.close)
        self.browser.force_login(self.user)

    def test_resource_registration_never_grants_a_personal_workflow(self):
        self.assertEqual(self.browser.get("/plugins/ifc-editor/").status_code, 404)
        self.assertEqual(Path(finders.find("wasm/example_plugin.wasm")),
                         EDITOR_ROOT / "static/wasm/example_plugin.wasm")
        UserPluginSelection.objects.create(user=self.user, plugin=self.plugin)
        self.assertContains(self.browser.get("/plugins/ifc-editor/"), "data-ifc-editor")
        self.plugin.enabled = False
        self.plugin.save(update_fields=["enabled"])
        self.assertEqual(self.browser.get("/plugins/ifc-editor/").status_code, 404)
        # Assets are public deployment resources; runtime route access stays
        # controlled by the current database record and personal selection.
        self.assertEqual(Path(finders.find("wasm/example_plugin.wasm")),
                         EDITOR_ROOT / "static/wasm/example_plugin.wasm")
