"""URL-backed plugin tabs over Bolt's browser transport and owned Django forms."""
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path
import tempfile
from unittest.mock import patch
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django_bolt import BoltAPI

from apps.plugin_manager.models import PluginRecord, UserPluginSelection
from apps.plugin_manager.registry import NAV_ITEM_EXTENSION_POINT, PluginRegistry
from apps.plugins.bim_model_manager import PLUGIN_ID, plugin_manifest
from apps.shared.ifc_extractor.test_material_assessment import IfcPassportTests
from apps.shared.models import BuildingLocation, BuildingMetrics, CadevilDocument, CalculationConfig, ConfigUpload, FileUpload
from tests.bolt_browser import BoltBrowser


TAB_HEADINGS = {
    "models": "BIM model manager",
    "map": "Building map",
    "references": "Reference configurations",
    "editor": "Configuration editor",
    "calculate": "Material passport calculation",
    "compare": "Compare building assessments",
}
GUID = "00tMo7QcxqWdIGvc4sMN2A"
LOCATIONS = {"ifc_sha256": "a" * 64, "buildings": [{
    "guid": GUID, "name": "Controlled building", "site_name": "Controlled site",
    "latitude": 48.2, "longitude": 16.37, "status": "located", "source": "ifc_site",
    "message": "Declared site location", "crs": "EPSG:4326",
}]}
LOOKUP = {
    "country": {"country": "AT", "status": "routed", "label": "Austria"},
    "energy": {"status": "unavailable", "note": "Controlled provider unavailable"},
    "planning": {"status": "unavailable", "message": "Controlled planning unavailable"},
    "utilities": {"status": "missing", "items": [], "note": "No controlled tariff"},
}


class WorkspaceMarkup(HTMLParser):
    """Read semantic links, boundaries and headings without depending on whitespace."""
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, html):
        super().__init__()
        self.stack = []
        self.elements = []
        self.tabs = []
        self.workspace = []
        self.panels = []
        self.panel_headings = []
        self.menu_links = []
        self.heading_text = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.elements.append((tag, attributes))
        if "data-plugin-workspace" in attributes:
            self.workspace.append(attributes)
        if "data-workspace-tab" in attributes:
            self.tabs.append(attributes)
        if attributes.get("role") == "tabpanel":
            self.panels.append(attributes)
        if tag == "a" and any(values.get("id") == "menu-popover" for _, values in self.stack):
            self.menu_links.append(attributes)
        if tag == "h1" and any(values.get("role") == "tabpanel" for _, values in self.stack):
            self.heading_text = []
        if tag not in self.VOID_TAGS:
            self.stack.append((tag, attributes))

    def handle_data(self, data):
        if self.heading_text is not None:
            self.heading_text.append(data)

    def handle_endtag(self, tag):
        if tag == "h1" and self.heading_text is not None:
            self.panel_headings.append("".join(self.heading_text).strip())
            self.heading_text = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


@override_settings(STATIC_URL="/static/")
class PluginWorkspaceIntegrationTests(TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        templates = deepcopy(settings.TEMPLATES)
        processors = templates[0]["OPTIONS"].setdefault("context_processors", [])
        processor = "apps.plugin_manager.context_processors.plugin_nav_items"
        if processor not in processors:
            processors.append(processor)
        isolated = override_settings(MEDIA_ROOT=self.folder / "media", TEMPLATES=templates,
                                     CITYJSON_CACHE_ROOT=self.folder / "cityjson")
        isolated.enable(); self.addCleanup(isolated.disable)
        self.user = get_user_model().objects.create_user(username="tab-owner")
        self.other = get_user_model().objects.create_user(username="tab-other")
        self.unselected_staff = get_user_model().objects.create_user(username="tab-staff", is_staff=True)
        self.plugin, _ = PluginRecord.objects.update_or_create(plugin_id=PLUGIN_ID,
            defaults={"enabled": True, "error": "", "compatibility": "both"})
        for user in (self.user, self.other):
            UserPluginSelection.objects.create(user=user, plugin=self.plugin)
        model = IfcPassportTests().model()
        self.element = model.by_type("IfcWall")[0].GlobalId
        self.ifc = model.to_string().encode()
        self.upload = FileUpload.objects.create(user=self.user, description="Owned tab model",
            document=SimpleUploadedFile("owned.ifc", self.ifc))
        self.private_upload = FileUpload.objects.create(user=self.other, description="Other owner secret model",
            document=SimpleUploadedFile("private.ifc", self.ifc))
        self.document = self.assessment(self.user, self.upload, "Owned tab assessment")
        self.private_document = self.assessment(self.other, self.private_upload, "Other owner secret assessment")
        from config.api import api as main_api
        from apps.mycelium.api import api as home_api
        from apps.plugin_manager.api import api as manager_api
        combined = BoltAPI(trailing_slash="keep", django_middleware=True)
        for child in (main_api, home_api, manager_api):
            combined.mount("", child)
        self.client = BoltBrowser(api=combined); self.addCleanup(self.client.close)
        self.client.force_login(self.user)
        self.url = reverse("bim:workspace")
        map_source = patch("apps.plugins.bim_model_manager.exchange_pages._source_locations", return_value=deepcopy(LOCATIONS))
        self.map_source = map_source.start(); self.addCleanup(map_source.stop)
        context_source = patch("apps.plugins.bim_model_manager.location_pages._source_locations", return_value=deepcopy(LOCATIONS))
        self.context_source = context_source.start(); self.addCleanup(context_source.stop)
        lookup = patch("apps.plugins.bim_model_manager.location_pages.lookup_location", return_value=deepcopy(LOOKUP))
        self.lookup = lookup.start(); self.addCleanup(lookup.stop)

    def assessment(self, user, upload, description):
        document = CadevilDocument.objects.create(user=user, upload=upload, group=user.groups.first(), description=description)
        BuildingMetrics.objects.create(project=document, assessment_report={
            "materials": {}, "rows": [], "inventory": [], "elements": {}, "building": {},
            "complete": False, "options": {"years": 50},
        })
        return document

    def assert_workspace(self, response, selected, *, status=200, full=False, heading=None):
        self.assertEqual(response.status_code, status)
        html = response.content.decode()
        markup = WorkspaceMarkup(html)
        self.assertEqual(sum(attributes.get("id") == "content-container" for _, attributes in markup.elements), 1)
        self.assertEqual(len(markup.workspace), 1)
        self.assertEqual(markup.workspace[0]["data-plugin-workspace"], "bim")
        self.assertEqual([tab["data-workspace-tab"] for tab in markup.tabs], list(TAB_HEADINGS))
        active = [tab for tab in markup.tabs if tab.get("aria-current") == "page"]
        self.assertEqual([tab["data-workspace-tab"] for tab in active], [selected])
        self.assertEqual(len(markup.panels), 1)
        self.assertEqual(markup.panels[0]["aria-labelledby"], active[0]["id"])
        self.assertEqual(markup.workspace[0]["data-active-tab-id"], active[0]["id"])
        for tab in markup.tabs:
            canonical = self.url + "?tab=" + tab["data-workspace-tab"]
            self.assertEqual(tab["href"], canonical)
            self.assertEqual(tab["hx-get"], canonical)
            self.assertEqual(tab["hx-target"], "#content-container")
            self.assertEqual(tab["hx-swap"], "outerHTML")
            self.assertEqual(tab["hx-push-url"], "true")
        self.assertEqual("<html" in html, full)
        if heading is not None:
            self.assertEqual(markup.panel_headings, [heading])
        return markup

    def test_six_canonical_tabs_render_only_the_requested_owned_panel(self):
        for selected, heading in TAB_HEADINGS.items():
            for full in (True, False):
                with self.subTest(tab=selected, full=full):
                    headers = {} if full else {"HTTP_HX_REQUEST": "true"}
                    response = self.client.get(self.url, {"tab": selected}, **headers)
                    self.assert_workspace(response, selected, full=full, heading=heading)
                    self.assertNotContains(response, "Other owner secret")
        self.assertEqual(self.map_source.call_count, 2)
        self.context_source.assert_not_called()
        self.lookup.assert_not_called()

    def test_default_models_and_unknown_tabs_do_not_prepare_other_panels(self):
        self.assert_workspace(self.client.get(self.url, HTTP_HX_REQUEST="true"), "models", heading=TAB_HEADINGS["models"])
        for tab in ("unknown", "../map", ""):
            self.assertEqual(self.client.get(self.url, {"tab": tab}).status_code, 404)
        self.map_source.assert_not_called()
        self.context_source.assert_not_called()
        self.lookup.assert_not_called()
        self.assertIn(self.client.post(self.url, {}).status_code, (404, 405))
        self.assertEqual(FileUpload.objects.count(), 2)

    def test_login_personal_selection_and_runtime_state_gate_all_tabs(self):
        for user in (None, self.unselected_staff):
            self.client.logout()
            if user:
                self.client.force_login(user)
            for tab in (*TAB_HEADINGS, "unknown"):
                response = self.client.get(self.url, {"tab": tab})
                self.assertEqual(response.status_code, 404 if user else 302)
                if not user:
                    target = urlsplit(response.url)
                    self.assertEqual(target.path, "/mycelium/login")
                    self.assertEqual(parse_qs(target.query)["next"], [self.url + "?tab=" + tab])
        self.client.force_login(self.user)
        for changes in ({"enabled": False}, {"enabled": True, "error": "Unavailable"},
                        {"error": "", "compatibility": "debug"}):
            PluginRecord.objects.filter(pk=self.plugin.pk).update(**changes)
            with override_settings(DEBUG=False):
                for tab in TAB_HEADINGS:
                    self.assertEqual(self.client.get(self.url, {"tab": tab}).status_code, 404)
        self.map_source.assert_not_called()
        self.context_source.assert_not_called()
        self.lookup.assert_not_called()

    def test_plugin_contributes_one_workspace_entry_to_the_global_menu(self):
        local = PluginRegistry(); plugin_manifest().register(local)
        items = local.get_active(NAV_ITEM_EXTENSION_POINT, enabled_ids={PLUGIN_ID})
        self.assertEqual([(item.label, item.url, item.full_page) for item in items], [("BIM Workspace", self.url, False)])
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        markup = WorkspaceMarkup(home.content.decode())
        bim_links = [link for link in markup.menu_links if link.get("href", "").startswith("/plugins/bim/")]
        self.assertEqual([link["href"] for link in bim_links], [self.url])
        self.assertEqual(bim_links[0]["hx-get"], self.url)
        self.assertFalse(markup.workspace)
        PluginRecord.objects.filter(pk=self.plugin.pk).update(enabled=False)
        markup = WorkspaceMarkup(self.client.get("/").content.decode())
        self.assertFalse([link for link in markup.menu_links if link.get("href", "").startswith("/plugins/bim/")])

    def test_old_primary_get_aliases_remain_usable_and_preserve_encoded_query_context(self):
        names = {"models": "bim:model_manager", "map": "bim:building_map",
                 "references": "bim:configuration_library", "editor": "bim:config_editor",
                 "calculate": "material_passport:calculate", "compare": "material_passport:compare"}
        query = {"model": str(self.upload.pk), "element": self.element, "page": "2",
                 "note": "Wien & Süd / floor + 2?", "tab": "unknown"}
        for tab, name in names.items():
            response = self.client.get(reverse(name) + "?" + urlencode(query, quote_via=quote), HTTP_HX_REQUEST="true")
            self.assert_workspace(response, tab, heading=TAB_HEADINGS[tab])
            history = urlsplit(response["HX-Push-Url"])
            self.assertEqual(history.path, self.url)
            self.assertEqual(parse_qs(history.query), {"tab": [tab], "model": [str(self.upload.pk)],
                "element": [self.element], "page": ["2"], "note": ["Wien & Süd / floor + 2?"]})
            restored = self.client.get(response["HX-Push-Url"], HTTP_HX_REQUEST="true")
            self.assert_workspace(restored, tab, heading=TAB_HEADINGS[tab])
            self.assertEqual(restored.context["request"].GET["note"], query["note"])
            self.assertEqual(restored.context["request"].GET["element"], self.element)
            self.assertEqual(restored.context["request"].GET["page"], "2")
            if tab == "calculate":
                self.assertEqual(restored.context["form"].initial["model"], self.upload)
            elif tab == "map":
                self.assertEqual(restored.context["pagination"].number, response.context["pagination"].number)

    def test_model_prefill_and_viewer_deep_links_retain_owned_identifiers(self):
        response = self.client.get(self.url, {"tab": "calculate", "model": self.upload.pk}, HTTP_HX_REQUEST="true")
        markup = self.assert_workspace(response, "calculate")
        selected = [attributes for tag, attributes in markup.elements if tag == "input"
                    and attributes.get("name") == "model" and "checked" in attributes]
        self.assertEqual([attributes["value"] for attributes in selected], [str(self.upload.pk)])
        self.assertEqual(response.context["form"].initial["model"], self.upload)
        response = self.client.get(reverse("bim:viewer", args=[self.upload.pk]), {
            "assessment": self.document.pk, "element": self.element, "from": "map"}, HTTP_HX_REQUEST="true")
        markup = self.assert_workspace(response, "map")
        viewer = next(attributes for _, attributes in markup.elements if attributes.get("id") == "viewer-app")
        self.assertEqual(viewer["data-selected-element"], self.element)
        self.assertEqual(parse_qs(urlsplit(viewer["data-metadata-url"]).query), {"assessment": [str(self.document.pk)]})
        self.assertEqual(response.context["selected_assessment"], str(self.document.pk))
        self.assertTrue(response.context["from_map"])
        self.assert_workspace(self.client.get(reverse("bim:viewer", args=[self.upload.pk]), HTTP_HX_REQUEST="true"), "models")
        foreign = self.client.get(reverse("bim:viewer", args=[self.upload.pk]), {"assessment": self.private_document.pk})
        self.assertEqual(foreign.status_code, 404)

    def test_validation_responses_remain_in_their_original_tabs_without_saving(self):
        reference = ConfigUpload.objects.create(user=self.user, description="Controlled reference",
            document=SimpleUploadedFile("reference.csv", b"Material;Dichte\nConcrete;2300\n"))
        CalculationConfig.objects.create(user=self.user, upload=reference,
            config={"header": ["Dichte"], "data": {"Concrete": {"Dichte": 2300}}})
        cases = [
            ("bim:model_manager", {"description": "Missing IFC document"}, "models", 400),
            ("bim:configuration_library", {"description": "Missing reference document"}, "references", 400),
            ("bim:config_editor", {"source": "stale"}, "editor", 409),
            ("material_passport:calculate", {"model": self.upload.pk}, "calculate", 200),
            ("material_passport:compare", {"models": [self.document.pk]}, "compare", 200),
            ("bim:import_cityjson", {"description": "Missing CityJSON document"}, "models", 400),
        ]
        for route, data, tab, status in cases:
            with self.subTest(route=route):
                response = self.client.post(reverse(route), data, HTTP_HX_REQUEST="true")
                markup = self.assert_workspace(response, tab, status=status)
                forms = [attributes for tag, attributes in markup.elements if tag == "form"]
                self.assertTrue(any(form.get("action") == reverse(route) for form in forms))
                self.assertNotContains(response, "Other owner secret", status_code=status)
                if "form" in response.context:
                    self.assertTrue(response.context["form"].is_bound)
                    self.assertTrue(response.context["form"].errors)
                else:
                    self.assertTrue(response.context["errors"])
                if tab in ("calculate", "compare"):
                    field = "model" if tab == "calculate" else "models"
                    value = str(self.upload.pk if tab == "calculate" else self.document.pk)
                    self.assertTrue(any(tag == "input" and attributes.get("name") == field
                        and attributes.get("value") == value and "checked" in attributes
                        for tag, attributes in markup.elements))
        self.assertEqual(FileUpload.objects.count(), 2)
        self.assertEqual(ConfigUpload.objects.count(), 1)
        self.assertEqual(CalculationConfig.objects.get(user=self.user).upload_id, reference.pk)
        self.assertEqual(CadevilDocument.objects.count(), 2)
        self.assertEqual(Path(self.upload.document.path).read_bytes(), self.ifc)

    def test_detail_pages_choose_parent_tabs_and_inline_lookup_stays_unwrapped(self):
        cases = [(reverse("bim:export_cityjson", args=[self.upload.pk]), "models"),
                 (reverse("bim:building_location", args=[self.upload.pk, GUID]), "map"),
                 (reverse("bim:building_context", args=[self.upload.pk, GUID]), "map"),
                 (reverse("material_passport:report", args=[self.document.pk]), "calculate")]
        for route, tab in cases:
            self.assert_workspace(self.client.get(route, HTTP_HX_REQUEST="true"), tab)
        invalid = self.client.post(reverse("bim:building_location", args=[self.upload.pk, GUID]),
            {"latitude": 91, "longitude": 16.37}, HTTP_HX_REQUEST="true")
        self.assert_workspace(invalid, "map", status=400)
        self.assertIn("latitude", invalid.context["form"].errors)
        self.assertFalse(BuildingLocation.objects.exists())
        route = reverse("bim:building_context", args=[self.upload.pk, GUID])
        for target in ("div#building-map-context-7", "section#building-context-results"):
            inline = self.client.get(route, HTTP_HX_REQUEST="true", HTTP_HX_TARGET=target)
            self.assertEqual(inline.status_code, 200)
            markup = WorkspaceMarkup(inline.content.decode())
            self.assertFalse(markup.workspace)
            self.assertFalse(markup.tabs)
            self.assertFalse(any(attributes.get("id") == "content-container" for _, attributes in markup.elements))
            self.assertTrue(any(attributes.get("id") == target.split("#")[-1] for _, attributes in markup.elements))
            self.assertIn("HX-Target", inline["Vary"])

    def test_history_restore_and_htmx_full_requests_keep_one_full_workspace_shell(self):
        for extra in ({"HTTP_HX_REQUEST_TYPE": "full"}, {"HTTP_HX_HISTORY_RESTORE_REQUEST": "true"}):
            response = self.client.get(self.url, {"tab": "compare"}, HTTP_HX_REQUEST="true", **extra)
            self.assert_workspace(response, "compare", full=True, heading=TAB_HEADINGS["compare"])
            self.assertIn("HX-Request-Type", response["Vary"])
            self.assertIn("HX-History-Restore-Request", response["Vary"])
        context = self.client.get(reverse("bim:building_context", args=[self.upload.pk, GUID]),
            HTTP_HX_REQUEST="true", HTTP_HX_TARGET="div#building-map-context-7", HTTP_HX_HISTORY_RESTORE_REQUEST="true")
        self.assert_workspace(context, "map", full=True)

    def test_private_downloads_and_report_exports_remain_raw_and_owner_scoped(self):
        download = self.client.get(reverse("bim:download_model", args=[self.upload.pk]))
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, self.ifc)
        self.assertIn("attachment", download["Content-Disposition"])
        self.assertEqual(self.client.get(reverse("bim:download_model", args=[self.private_upload.pk])).status_code, 404)
        for kind, media in (("json", "application/json"), ("csv", "text/csv"), ("recovery_csv", "text/csv")):
            response = self.client.get(reverse("material_passport:report", args=[self.document.pk]), {"download": kind})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response["Content-Type"].startswith(media))
            self.assertIn("attachment", response["Content-Disposition"])
            self.assertNotContains(response, "data-plugin-workspace")
            private = self.client.get(reverse("material_passport:report", args=[self.private_document.pk]), {"download": kind})
            self.assertEqual(private.status_code, 404)

    def test_public_demo_never_gets_private_workspace_tabs(self):
        for authenticated in (True, False):
            if not authenticated:
                self.client.logout()
            for path in ("/", "/demo"):
                response = self.client.get(path, HTTP_HX_REQUEST="true")
                self.assertEqual(response.status_code, 200)
                markup = WorkspaceMarkup(response.content.decode())
                self.assertFalse(markup.workspace)
                self.assertFalse(markup.tabs)
                self.assertNotContains(response, "Other owner secret")
                self.assertEqual(sum(attributes.get("id") == "content-container" for _, attributes in markup.elements), 1)
