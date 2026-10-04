"""Regression coverage for audited native BIM form and CSV boundaries."""
import csv
import io
import tempfile
from unittest.mock import patch

import ifcopenshell.api.aggregate
import ifcopenshell.api.root
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.plugin_manager.models import PluginRecord, UserPluginSelection
from apps.plugins.bim_model_manager import PLUGIN_ID
from apps.shared.ifc_extractor import test_material_assessment as fixtures
from apps.shared.models import (BuildingLocation, BuildingMetrics, CadevilDocument,
                                CalculationConfig, ConfigUpload, FileUpload, ModelConversion)
from tests.bolt_browser import BoltBrowser


class BackendBoundaryTests(TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        settings = override_settings(MEDIA_ROOT=folder.name, MEDIA_URL="/user_uploads/")
        settings.enable()
        self.addCleanup(settings.disable)
        self.user = get_user_model().objects.create_user(username="audit-owner")
        plugin, _ = PluginRecord.objects.update_or_create(plugin_id=PLUGIN_ID, defaults={"enabled": True})
        UserPluginSelection.objects.create(user=self.user, plugin=plugin)
        self.client = BoltBrowser()
        self.addCleanup(self.client.close)
        self.client.force_login(self.user)

    def test_empty_posts_report_required_fields_in_the_selected_workspace(self):
        routes = [
            ("bim:model_manager", 400, "models", {"document"}),
            ("bim:configuration_library", 400, "references", {"document"}),
            ("bim:import_cityjson", 400, "models", {"document"}),
            ("material_passport:calculate", 200, "calculate",
             {"model_file", "reference_file", "replacement_boundary", "lca_averaging", "grade_weighting"}),
            ("material_passport:compare", 200, "compare", {"models"}),
        ]
        with patch("apps.shared.assessment_web.assess_ifc") as assess:
            for route, status, tab, required in routes:
                with self.subTest(route=route):
                    response = self.client.post(reverse(route), {}, HTTP_HX_REQUEST="true")
                    self.assertEqual(response.status_code, status)
                    self.assertTrue(response.context["form"].is_bound)
                    self.assertEqual(set(response.context["form"].errors), required)
                    self.assertEqual([t["key"] for t in response.context["plugin_workspace"]["tabs"] if t["selected"]], [tab])
                    self.assertContains(response, 'id="content-container"', count=1, status_code=status)
            assess.assert_not_called()
        for model in (FileUpload, ConfigUpload, CadevilDocument, BuildingMetrics, ModelConversion):
            self.assertFalse(model.objects.exists(), model.__name__)

    def test_empty_location_post_keeps_source_and_existing_override(self):
        model = fixtures.IfcPassportTests().model()
        building = ifcopenshell.api.root.create_entity(model, ifc_class="IfcBuilding", name="Audit house")
        ifcopenshell.api.aggregate.assign_object(model, products=[building], relating_object=model.by_type("IfcProject")[0])
        original = model.to_string().encode()
        upload = FileUpload.objects.create(user=self.user, document=SimpleUploadedFile("house.ifc", original))
        location = BuildingLocation.objects.create(upload=upload, guid=building.GlobalId,
                                                   latitude=48.2, longitude=16.3, source="manual")
        response = self.client.post(reverse("bim:building_location", args=[upload.pk, building.GlobalId]), {},
                                    HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.context["form"].is_bound)
        self.assertEqual(set(response.context["form"].errors), {"latitude", "longitude"})
        self.assertEqual([t["key"] for t in response.context["plugin_workspace"]["tabs"] if t["selected"]], ["map"])
        location.refresh_from_db()
        self.assertEqual((location.latitude, location.longitude), (48.2, 16.3))
        with upload.document.open("rb") as source:
            self.assertEqual(source.read(), original)

    def test_csv_download_neutralizes_whitespace_prefixed_formulas_without_changing_numbers_or_source(self):
        source = b"Original reference contents"
        upload = ConfigUpload.objects.create(user=self.user, document=SimpleUploadedFile("reference.csv", source))
        headers = [" \t=HEADER()", "Amount", "Text"]
        config = {"header": headers, "data": {
            "\t@MATERIAL()": {headers[0]: " \t+FUNCTION()", "Amount": " -2", "Text": "\r\n-HYPERLINK()"},
            "ordinary": {headers[0]: "safe", "Amount": 3, "Text": "plain text"},
        }}
        active = CalculationConfig.objects.create(user=self.user, upload=upload, config=config)
        response = self.client.get(reverse("bim:download_csv"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment;", response["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(response.content.decode(), newline=""), delimiter=";"))
        self.assertEqual(rows, [
            ["Material", "' \t=HEADER()", "Amount", "Text"],
            ["'\t@MATERIAL()", "' \t+FUNCTION()", " -2", "'\r\n-HYPERLINK()"],
            ["ordinary", "safe", "3", "plain text"],
        ])
        active.refresh_from_db()
        self.assertEqual(active.config, config)
        with upload.document.open("rb") as original:
            self.assertEqual(original.read(), source)

    def test_raw_upload_urls_are_not_public_routes(self):
        source = b"private IFC source"
        upload = FileUpload.objects.create(user=self.user, document=SimpleUploadedFile("private.ifc", source))
        raw_url = upload.document.url
        self.client.logout()
        response = self.client.get(raw_url)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(source, response.content)
        self.assertEqual(self.client.get(reverse("bim:download_model", args=[upload.pk])).status_code, 302)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(raw_url).status_code, 404)
        self.assertEqual(self.client.get(reverse("bim:download_model", args=[upload.pk])).content, source)

    def test_uploaded_reference_error_is_plain_text_and_does_not_change_selection(self):
        malicious = '<img src=x onerror=alert(1)>'
        content = ('{"' + malicious + '": {}, "' + malicious + '": {}}').encode()
        upload = ConfigUpload.objects.create(user=self.user, document=SimpleUploadedFile("invalid.json", content))
        response = self.client.post(reverse("bim:save_config"), {"upload": str(upload.pk)})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
        self.assertIn(malicious.encode(), response.content)
        self.assertFalse(CalculationConfig.objects.filter(user=self.user).exists())
