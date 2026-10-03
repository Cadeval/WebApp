"""Native Bolt journey, ownership and storage integrity for GIS exchange."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import ifcopenshell.api.aggregate
import ifcopenshell.api.root
import ifcopenshell.api.spatial
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from tests.bolt_browser import BoltBrowser
from apps.plugin_manager.models import PluginRecord, UserPluginSelection
from apps.plugins.bim_model_manager import PLUGIN_ID
from apps.shared.models import FileUpload, ModelConversion, BuildingLocation
from apps.shared.ifc_extractor.test_material_assessment import IfcPassportTests


def city_model():
    return {"type": "CityJSON", "version": "1.1", "transform": {"scale": [1, 1, 1], "translate": [0, 0, 0]},
            "CityObjects": {"house": {"type": "Building", "attributes": {"name": "City house", "year": 2020},
                "geometry": [{"type": "MultiSurface", "lod": "2", "boundaries": [
                    [[0, 3, 2, 1]], [[4, 5, 6, 7]], [[0, 1, 5, 4]], [[1, 2, 6, 5]], [[2, 3, 7, 6]], [[3, 0, 4, 7]]]}]}},
            "vertices": [[0,0,0],[10,0,0],[10,10,0],[0,10,0],[0,0,5],[10,0,5],[10,10,5],[0,10,5]]}


class CityJSONPageTests(TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.media = Path(self.folder.name) / "media"
        settings = override_settings(MEDIA_ROOT=self.media, CITYJSON_CACHE_ROOT=Path(self.folder.name)/"cache")
        settings.enable(); self.addCleanup(settings.disable)
        self.user = get_user_model().objects.create_user(username="city-owner", password="local-test-only")
        self.other = get_user_model().objects.create_user(username="city-other")
        self.plugin, _ = PluginRecord.objects.update_or_create(plugin_id=PLUGIN_ID, defaults={"enabled": True})
        for user in (self.user, self.other):
            UserPluginSelection.objects.create(user=user, plugin=self.plugin)
        self.client = BoltBrowser(); self.addCleanup(self.client.close); self.client.force_login(self.user)
        self.raw = json.dumps(city_model()).encode()

    def import_model(self):
        response = self.client.post(reverse("bim:import_cityjson"), {
            "description": "Imported test house", "document": SimpleUploadedFile("house.city.json", self.raw), "name_attribute": "name"})
        self.assertEqual(response.status_code, 302, response.content[:500])
        return FileUpload.objects.get(user=self.user)

    def upload_ifc(self, user=None):
        model = IfcPassportTests().model()
        project = model.by_type("IfcProject")[0]
        site = ifcopenshell.api.root.create_entity(model, ifc_class="IfcSite", name="Controlled site")
        site.RefLatitude, site.RefLongitude = (48, 12, 0), (16, 22, 0)
        building = ifcopenshell.api.root.create_entity(model, ifc_class="IfcBuilding", name="Controlled building")
        ifcopenshell.api.aggregate.assign_object(model, products=[site], relating_object=project)
        ifcopenshell.api.aggregate.assign_object(model, products=[building], relating_object=site)
        ifcopenshell.api.spatial.assign_container(model, products=model.by_type("IfcWall"), relating_structure=building)
        return FileUpload.objects.create(user=user or self.user, description="Owned house", document=SimpleUploadedFile("house.ifc", model.to_string().encode())), building.GlobalId

    def test_import_preserves_source_provenance_and_viewer_journey(self):
        upload = self.import_model()
        self.assertTrue(upload.document.name.endswith(".ifc"))
        conversion = ModelConversion.objects.get(upload=upload)
        self.assertEqual(conversion.source.read(), self.raw)
        self.assertEqual(conversion.metadata["lod"], "2")
        self.assertEqual(len(conversion.output_sha256), 64)
        viewer = self.client.get(reverse("bim:viewer", args=[upload.pk]), HTTP_HX_REQUEST="true")
        self.assertContains(viewer, "CityJSON import")
        self.assertContains(viewer, "id=\"content-container\"", count=1)
        response = self.client.get(reverse("bim:cityjson_source", args=[upload.pk]))
        self.assertEqual(response.content, self.raw)

    def test_invalid_and_unsupported_cityjson_have_actionable_errors_without_files(self):
        for raw in (b'{"type":"FeatureCollection"}', b"not json"):
            response = self.client.post(reverse("bim:import_cityjson"), {"document": SimpleUploadedFile("bad.json", raw)})
            self.assertEqual(response.status_code, 400)
        self.assertFalse(FileUpload.objects.exists())
        self.assertFalse(ModelConversion.objects.exists())
        self.assertFalse(list(self.media.rglob("*")))

    def test_storage_rollback_removes_generated_and_original_files(self):
        with patch.object(ModelConversion, "save", side_effect=RuntimeError("controlled failure")):
            response = self.client.post(reverse("bim:import_cityjson"), {"document": SimpleUploadedFile("house.city.json", self.raw)})
            self.assertEqual(response.status_code, 500)
        self.assertFalse(FileUpload.objects.exists())
        self.assertFalse([p for p in self.media.rglob("*") if p.is_file()])

    def test_delete_converted_model_cleans_retained_source_after_commit(self):
        upload = self.import_model()
        source = Path(upload.conversion.source.path)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("bim:delete_model", args=[upload.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(source.exists())
        self.assertFalse(ModelConversion.objects.exists())

    def test_all_exchange_routes_require_owner_and_workflow(self):
        upload = self.import_model()
        guid = __import__('ifcopenshell').open(upload.document.path).by_type('IfcBuilding')[0].GlobalId
        routes = [reverse(name, args=args) for name, args in [
            ("bim:cityjson_source", [upload.pk]), ("bim:export_cityjson", [upload.pk]),
            ("bim:download_cityjson", [upload.pk]), ("bim:building_location", [upload.pk,guid])]]
        self.client.force_login(self.other)
        for route in routes:
            self.assertEqual(self.client.get(route).status_code, 404, route)
        self.assertNotContains(self.client.get(reverse("bim:building_map")), "Imported test house")
        self.client.force_login(self.user)
        UserPluginSelection.objects.filter(user=self.user).delete()
        for route in routes + [reverse("bim:import_cityjson"), reverse("bim:building_map")]:
            self.assertEqual(self.client.get(route).status_code, 404, route)

    def test_anonymous_exchange_redirects_and_mutations_require_csrf(self):
        self.client.logout()
        for name in ("bim:building_map", "bim:import_cityjson"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)
        client = BoltBrowser(csrf=False); self.addCleanup(client.close); client.force_login(self.user)
        self.assertEqual(client.post(reverse("bim:import_cityjson"), {}).status_code, 403)

    def test_map_locations_are_owned_source_derived_and_htmx_fragments(self):
        upload, guid = self.upload_ifc()
        self.upload_ifc(self.other)
        response = self.client.get(reverse("bim:building_map"), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="content-container"', count=1)
        rows = response.context["buildings"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["upload_id"], str(upload.pk))
        self.assertAlmostEqual(rows[0]["latitude"], 48.2)
        self.assertEqual(rows[0]["source"], "ifc_site")

    def test_manual_location_updates_only_one_upload_and_reset_restores_source(self):
        upload, guid = self.upload_ifc()
        route = reverse("bim:building_location", args=[upload.pk,guid])
        self.assertEqual(self.client.post(route, {"latitude": "48.3", "longitude": "16.4", "note": "Owner survey"}).status_code, 302)
        row = self.client.get(reverse("bim:building_map")).context["buildings"][0]
        self.assertEqual((row["latitude"], row["longitude"], row["source"]), (48.3,16.4,"manual"))
        original = Path(upload.document.path).read_bytes()
        self.assertEqual(self.client.post(route, {"action": "reset"}).status_code, 302)
        self.assertFalse(BuildingLocation.objects.exists())
        self.assertEqual(original, Path(upload.document.path).read_bytes())
        self.assertEqual(self.client.get(reverse("bim:building_map")).context["buildings"][0]["source"], "ifc_site")

    def test_unknown_building_and_invalid_coordinates_cannot_create_override(self):
        upload, guid = self.upload_ifc()
        self.assertEqual(self.client.get(reverse("bim:building_location", args=[upload.pk,"0"*22])).status_code, 404)
        route = reverse("bim:building_location", args=[upload.pk,guid])
        for latitude, longitude in [("91","0"),("0","181"),("nan","0")]:
            self.assertEqual(self.client.post(route, {"latitude":latitude,"longitude":longitude}).status_code, 400)
        self.assertFalse(BuildingLocation.objects.exists())

    def test_export_get_does_not_convert_and_post_creates_valid_download(self):
        upload, guid = self.upload_ifc()
        route = reverse("bim:export_cityjson", args=[upload.pk])
        with patch("apps.plugins.bim_model_manager.exchange_pages.model_cityjson") as convert:
            self.assertEqual(self.client.get(route).status_code, 200)
            convert.assert_not_called()
        self.assertEqual(self.client.get(reverse("bim:download_cityjson", args=[upload.pk])).status_code, 409)
        response = self.client.post(route)
        self.assertEqual(response.status_code, 200, response.content[:500])
        response = self.client.get(reverse("bim:download_cityjson", args=[upload.pk]))
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["version"], "1.1")
        self.assertIn(guid, data["CityObjects"])
