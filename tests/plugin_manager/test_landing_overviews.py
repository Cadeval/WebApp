"""Trusted app descriptions register at load and remain guest-only on home."""
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from django.apps import AppConfig, apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase, override_settings
from django_bolt import BoltAPI

from mycelium.api import api as home_api
from plugin_manager.django_resources import (
    get_overview_for_plugin,
    public_overview_templates,
    register_landing_overview,
)
from plugin_manager.resource_registry import OverviewTemplate, ResourceBundle, ResourceRegistry
from tests.bolt_browser import BoltBrowser


class Tags(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.tags = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class LandingOverviewDeclarationTests(SimpleTestCase):
    def fixture(self, root, name="fixture", *, compatibility="both"):
        package = root / "plugins" / name
        (package / "resources").mkdir(parents=True)
        (package / "resources/__init__.py").write_text("")
        template = package / "templates" / name / "overview.html"
        template.parent.mkdir(parents=True)
        template.write_text("<section>Fixture explanation</section>")
        metadata = {"version": 1, "plugin_id": "cadevil." + name,
                    "package": "plugins." + name,
                    "overview": {"template": name + "/overview.html", "compatibility": compatibility}}
        (package / "resources.json").write_text(json.dumps(metadata))
        return package, metadata

    def test_neutral_registry_keeps_typed_owned_overview(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, _ = self.fixture(root)
            bundle, = ResourceRegistry.from_builtins(root, {"fixture": "fixture:manifest"}).bundles()
            self.assertEqual(bundle.overview, OverviewTemplate("fixture/overview.html"))
            self.assertEqual(bundle.root, package.resolve())

    def test_foreign_traversal_invalid_type_and_unknown_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, metadata = self.fixture(root)
            invalid = [None, "fixture/overview.html", {},
                       {"template": "other/overview.html", "compatibility": "both"},
                       {"template": "fixture/../overview.html", "compatibility": "both"},
                       {"template": "/fixture/overview.html", "compatibility": "both"},
                       {"template": "fixture/overview.html", "compatibility": []},
                       {"template": "fixture/overview.html", "compatibility": "other"},
                       {"template": "fixture/overview.html", "compatibility": "both", "unknown": True}]
            for value in invalid:
                with self.subTest(value=value):
                    (package / "resources.json").write_text(json.dumps({**metadata, "overview": value}))
                    with self.assertRaises(ValueError):
                        ResourceRegistry.from_builtins(root, {"fixture": "fixture:manifest"})

    def test_symlinked_template_file_or_ancestor_is_rejected(self):
        for relative in ("templates/fixture", "templates/fixture/overview.html"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                package, _ = self.fixture(root)
                original = package / relative
                outside = root / "outside"
                original.rename(outside)
                original.symlink_to(outside, target_is_directory=outside.is_dir())
                with self.assertRaisesRegex(ValueError, "symlinks"):
                    ResourceRegistry.from_builtins(root, {"fixture": "fixture:manifest"})

    def test_missing_or_oversized_owned_template_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, _ = self.fixture(root)
            template = package / "templates/fixture/overview.html"
            template.unlink()
            for oversized in (False, True):
                if oversized:
                    template.write_text(" " * 65537)
                with self.assertRaisesRegex(ValueError, "small, owned"):
                    ResourceRegistry.from_builtins(root, {"fixture": "fixture:manifest"})

    def app(self, root, name, compatibility="both"):
        module = ModuleType(name)
        module.__file__ = str(root / name / "__init__.py")
        config = AppConfig(name, module)
        config.path = str(root / name)
        path = Path(config.path) / "templates" / name
        path.mkdir(parents=True)
        (path / "overview.html").write_text("<section>App overview</section>")
        register_landing_overview(config, OverviewTemplate(name + "/overview.html", compatibility))
        return config

    def test_registration_is_idempotent_but_cannot_replace_a_contribution(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self.app(Path(temporary), "fixture")
            register_landing_overview(config, OverviewTemplate("fixture/overview.html"))
            second = Path(config.path) / "templates/fixture/second.html"
            second.write_text("Another explanation")
            with self.assertRaisesRegex(ValueError, "only one"):
                register_landing_overview(config, OverviewTemplate("fixture/second.html"))

    def test_public_collection_is_deterministic_and_omits_debug_only_apps(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configs = [self.app(root, "zeta", "production"), self.app(root, "developer", "debug"), self.app(root, "alpha")]
            def template(name):
                owner = name.partition("/")[0]
                return SimpleNamespace(origin=SimpleNamespace(name=str(root / owner / "templates" / name)))
            with patch("plugin_manager.django_resources.apps.get_app_configs", return_value=configs), \
                    patch("plugin_manager.django_resources.get_template", side_effect=template):
                self.assertEqual(public_overview_templates(), ("alpha/overview.html", "zeta/overview.html"))

    def test_loader_cannot_shadow_the_declared_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self.app(Path(temporary), "fixture")
            shadow = SimpleNamespace(origin=SimpleNamespace(name=str(Path(temporary) / "other.html")))
            with patch("plugin_manager.django_resources.apps.get_app_configs", return_value=[config]), \
                    patch("plugin_manager.django_resources.get_template", return_value=shadow):
                with self.assertRaisesRegex(ImproperlyConfigured, "owning"):
                    public_overview_templates()

    def test_plugin_lookup_uses_loaded_declarations_only(self):
        self.assertEqual(get_overview_for_plugin("cadevil.bim.model_manager"), "bim_model_manager/overview.html")
        self.assertEqual(get_overview_for_plugin("cadevil.example.editor"), "example_plugin/overview.html")
        self.assertEqual(get_overview_for_plugin("cadevil.rust-example.editor"), "rust_example_plugin/overview.html")
        self.assertIsNone(get_overview_for_plugin("unregistered.upload"))
        self.assertIsNone(get_overview_for_plugin("cadevil.mcp.native"))

    def test_private_debug_description_is_available_only_in_debug_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self.app(Path(temporary), "fixture", "debug")
            config.resource_bundle = ResourceBundle("cadevil.fixture", "plugins.fixture", Path(config.path), config.landing_overview)
            template = SimpleNamespace(origin=SimpleNamespace(name=str(Path(config.path) / "templates/fixture/overview.html")))
            with patch("plugin_manager.django_resources.apps.get_app_configs", return_value=[config]), \
                    patch("plugin_manager.django_resources.get_template", return_value=template):
                with override_settings(DEBUG=True):
                    self.assertEqual(get_overview_for_plugin("cadevil.fixture"), "fixture/overview.html")
                    self.assertEqual(public_overview_templates(), ())
                with override_settings(DEBUG=False):
                    self.assertIsNone(get_overview_for_plugin("cadevil.fixture"))

    def test_core_and_plugin_ready_hooks_register_separate_fragments(self):
        names = {config.name: config.landing_overview.template_name
                 for config in apps.get_app_configs() if hasattr(config, "landing_overview")}
        self.assertEqual(names, {
            "mycelium": "mycelium/overview.html",
            "plugin_manager": "plugin_manager/overview.html",
            "plugins.bim_model_manager.django": "bim_model_manager/overview.html",
            "plugins.example_plugin.resources": "example_plugin/overview.html",
            "plugins.rust_example_plugin.resources": "rust_example_plugin/overview.html",
        })


class GuestLandingPageTests(TestCase):
    def setUp(self):
        combined = BoltAPI(trailing_slash="keep", django_middleware=True)
        combined.mount("", home_api)
        self.browser = BoltBrowser(api=combined)
        self.addCleanup(self.browser.close)

    def test_guest_full_fragment_and_history_restore_have_one_boundary_and_top_demo(self):
        variants = ({}, {"HX-Request": "true"}, {"HX-Request": "true", "HX-History-Restore-Request": "true"})
        for headers in variants:
            with self.subTest(headers=headers):
                response = self.browser.get("/", headers=headers)
                self.assertEqual(response.status_code, 200)
                markup = response.content.decode()
                self.assertEqual('id="content-container"' in markup, True)
                self.assertEqual(markup.count('id="content-container"'), 1)
                self.assertEqual("<html" in markup, headers.get("HX-Request") != "true" or "HX-History-Restore-Request" in headers)
                self.assertEqual(markup.count("Open demo"), 1)
                self.assertLess(markup.index(">Open demo</a>"), markup.index('id="home-about-title"'))
                links = [attrs for tag, attrs in Tags(markup).tags if tag == "a" and attrs.get("href") == "/demo" and "journey-button" in attrs.get("class", "").split()]
                self.assertEqual(len(links), 1)
                self.assertEqual({key: links[0][key] for key in ("hx-get", "hx-target", "hx-swap", "hx-push-url")},
                                 {"hx-get": "/demo", "hx-target": "#content-container", "hx-swap": "outerHTML", "hx-push-url": "true"})
                for title in ("home-about-title", "home-mycelium-title", "home-catalog-title", "home-bim-title", "home-ifc-editor-title", "home-snake-title"):
                    self.assertIn('id="' + title + '"', markup)
                self.assertIn("provisional", markup)
                self.assertNotIn('id="home-workflow-title"', markup)

    def test_authenticated_home_keeps_actions_and_never_collects_readme_fragments(self):
        user = get_user_model().objects.create_user(username="landing-owner")
        self.browser.force_login(user)
        with patch("plugin_manager.django_resources.public_overview_templates", side_effect=AssertionError("Authenticated home must not collect public explanations")):
            for headers in ({}, {"HX-Request": "true"}):
                response = self.browser.get("/", headers=headers)
                self.assertEqual(response.status_code, 200)
                markup = response.content.decode()
                self.assertEqual(markup.count('id="content-container"'), 1)
                self.assertIn("Your workspace", markup)
                self.assertIn("Choose workflow tools", markup)
                self.assertIn("User settings", markup)
                self.assertIn('id="home-workflow-title"', markup)
                self.assertNotIn("Open demo", markup)
                for title in ("home-about-title", "home-mycelium-title", "home-catalog-title", "home-bim-title", "home-ifc-editor-title", "home-snake-title"):
                    self.assertNotIn('id="' + title + '"', markup)
