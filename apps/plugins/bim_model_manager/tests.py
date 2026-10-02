from pathlib import Path
from types import SimpleNamespace

from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.test import SimpleTestCase


class Model3DViewTemplateTests(SimpleTestCase):
    def render_viewer(self) -> str:
        return render_to_string(
            "webapp/model_3d_view.jinja2",
            {
                "document": SimpleNamespace(description="Campus model"),
                "upload_id": "31a7479d-8006-48ce-834a-e94a933d6762",
            },
        )

    def test_viewer_uses_external_module_with_model_url_configuration(self) -> None:
        content = self.render_viewer()

        self.assertIn("static/js/3d_view.js", content)
        self.assertIn(
            'data-model-url="/api/model_file/31a7479d-8006-48ce-834a-e94a933d6762/stream_gltf/"',
            content,
        )
        self.assertNotIn("import * as THREE", content)
        self.assertNotIn("const glbUrl", content)

    def test_application_shells_map_three_before_loading_modules(self) -> None:
        template_root = Path(__file__).parents[1] / "resources" / "templates"

        for template_name in ("index.jinja2", "base.jinja2"):
            with self.subTest(template_name=template_name):
                content = (template_root / template_name).read_text()
                import_map_position = content.find('<script type="importmap">')
                first_module_position = content.find('<script type="module"')

                self.assertGreaterEqual(import_map_position, 0)
                self.assertGreater(first_module_position, import_map_position)
                self.assertIn(
                    '"three": "https://cdn.jsdelivr.net/npm/three@0.184.0/build/three.module.js"',
                    content,
                )
                self.assertIn(
                    '"three/addons/": "https://cdn.jsdelivr.net/npm/three@0.184.0/examples/jsm/"',
                    content,
                )

        self.assertNotIn('type="importmap"', self.render_viewer())

    def test_viewer_exposes_selection_metrics_and_controls(self) -> None:
        content = self.render_viewer()

        self.assertIn('id="viewer-sidebar"', content)
        self.assertIn('id="selection-status"', content)
        self.assertIn('aria-live="polite"', content)
        self.assertIn('id="part-metrics"', content)
        self.assertIn('data-viewer-action="fit"', content)
        self.assertIn('data-viewer-action="toggle-grid"', content)
        self.assertIn('data-viewer-action="clear-selection"', content)

    def test_viewer_marks_toolbar_for_header_and_floats_sidebar_in_viewport(self) -> None:
        content = self.render_viewer()

        self.assertIn('data-viewer-header-content', content)
        self.assertRegex(
            content,
            r'(?s)<div id="viewer-viewport".*<aside id="viewer-sidebar".*</aside>\s*</div>',
        )

        stylesheet_path = finders.find("../../../resources/static/css/style.css")
        self.assertIsNotNone(stylesheet_path)
        stylesheet = Path(stylesheet_path).read_text()
        self.assertIn(".viewer-sidebar {\n    position: absolute;", stylesheet)

    def test_application_shells_expose_viewer_header_slot(self) -> None:
        template_root = Path(__file__).parents[1] / "resources" / "templates"

        for template_name in ("index.jinja2", "base.jinja2"):
            with self.subTest(template_name=template_name):
                content = (template_root / template_name).read_text()
                self.assertIn('id="viewer-header-slot"', content)

    def test_view_transition_name_is_scoped_to_page_chrome(self) -> None:
        stylesheet_path = finders.find("../../../resources/static/css/style.css")
        self.assertIsNotNone(stylesheet_path)
        stylesheet = Path(stylesheet_path).read_text()

        self.assertIn("body > header {\n    view-transition-name: site-header;", stylesheet)
        self.assertIn("body > footer {\n    view-transition-name: site-footer;", stylesheet)
        self.assertNotIn("\nheader {\n    view-transition-name: site-header;", stylesheet)
        self.assertNotIn("\nfooter {\n    view-transition-name: site-footer;", stylesheet)


class SafariLayoutCompatibilityTests(SimpleTestCase):
    @staticmethod
    def stylesheet() -> str:
        stylesheet_path = finders.find("../../../resources/static/css/style.css")
        if stylesheet_path is None:
            raise AssertionError("css/style.css was not found")
        return Path(stylesheet_path).read_text()

    def test_application_shells_declare_one_edge_to_edge_viewport(self) -> None:
        template_root = Path(__file__).parents[1] / "resources" / "templates"

        for template_name in ("index.jinja2", "base.jinja2"):
            with self.subTest(template_name=template_name):
                content = (template_root / template_name).read_text()
                self.assertEqual(content.count('name="viewport"'), 1)
                self.assertIn(
                    'content="width=device-width, initial-scale=1.0, viewport-fit=cover"',
                    content,
                )

    def test_fixed_chrome_accounts_for_safari_safe_areas(self) -> None:
        stylesheet = self.stylesheet()

        self.assertIn("--safe-area-top: env(safe-area-inset-top, 0px);", stylesheet)
        self.assertIn("--safe-area-right: env(safe-area-inset-right, 0px);", stylesheet)
        self.assertIn("--safe-area-bottom: env(safe-area-inset-bottom, 0px);", stylesheet)
        self.assertIn("--safe-area-left: env(safe-area-inset-left, 0px);", stylesheet)
        self.assertIn("--header-extent: calc(var(--header-height) + var(--safe-area-top));", stylesheet)
        self.assertIn("--footer-extent: calc(var(--footer-height) + var(--safe-area-bottom));", stylesheet)
        self.assertIn("padding-top: var(--safe-area-top);", stylesheet)
        self.assertIn("padding-right: var(--safe-area-right);", stylesheet)
        self.assertIn("padding-bottom: var(--safe-area-bottom);", stylesheet)
        self.assertIn("padding-left: var(--safe-area-left);", stylesheet)
        self.assertIn("margin-top: calc(var(--header-extent) + 0.625em);", stylesheet)
        self.assertIn("margin-bottom: calc(var(--footer-extent) + 0.625em);", stylesheet)
        self.assertIn("top: calc(var(--header-extent) + 0.25em);", stylesheet)

    def test_fixed_chrome_does_not_use_scrollbar_inclusive_viewport_width(self) -> None:
        stylesheet = self.stylesheet()

        self.assertRegex(
            stylesheet,
            r"(?s)header,\s*footer\s*\{[^}]*left:\s*0;[^}]*right:\s*0;",
        )
        self.assertNotRegex(
            stylesheet,
            r"(?s)header,\s*footer\s*\{[^}]*width:\s*100vw;",
        )

    def test_viewer_uses_dynamic_viewport_units_after_legacy_fallbacks(self) -> None:
        stylesheet = self.stylesheet()

        self.assertIn(
            "height: calc(100vh - var(--header-extent) - var(--footer-extent) - 1.5em);\n"
            "    height: calc(100dvh - var(--header-extent) - var(--footer-extent) - 1.5em);",
            stylesheet,
        )
        self.assertIn(
            "min-height: calc(100vh - var(--header-extent) - var(--footer-extent) - 1.5em);\n"
            "        min-height: calc(100dvh - var(--header-extent) - var(--footer-extent) - 1.5em);",
            stylesheet,
        )
        self.assertIn("min-height: 55vh;\n        min-height: 55dvh;", stylesheet)

    def test_full_height_panels_use_dynamic_viewport_units_after_fallbacks(self) -> None:
        stylesheet = self.stylesheet()

        self.assertRegex(
            stylesheet,
            r"(?s)\.config-editor-viewport\s*\{[^}]*"
            r"max-height:\s*85vh;[^}]*max-height:\s*85dvh;",
        )
        self.assertRegex(
            stylesheet,
            r"(?s)\.config-editor-pan\s*\{[^}]*"
            r"max-height:\s*85vh;[^}]*max-height:\s*85dvh;",
        )
        self.assertRegex(
            stylesheet,
            r"(?s)#svgContainer\s*\{[^}]*height:\s*80vh;[^}]*height:\s*80dvh;",
        )

    def test_webkit_fallbacks_precede_modern_visual_effects(self) -> None:
        stylesheet = self.stylesheet()

        self.assertIn("-webkit-text-size-adjust: 100%;", stylesheet)
        self.assertIn(
            "-webkit-backdrop-filter: blur(8px);\n    backdrop-filter: blur(8px);",
            stylesheet,
        )
        self.assertIn(
            "-webkit-backdrop-filter: blur(12px);\n    backdrop-filter: blur(12px);",
            stylesheet,
        )
        self.assertRegex(
            stylesheet,
            r"(?s)\.plugin-reload-status\s*\{[^}]*"
            r"border:\s*1px solid var\(--success-color\);[^}]*"
            r"border:\s*1px solid color-mix\(",
        )
        self.assertRegex(
            stylesheet,
            r"(?s)\.viewer-sidebar\s*\{[^}]*"
            r"background:\s*var\(--secondary-bg\);[^}]*"
            r"background:\s*color-mix\(",
        )
