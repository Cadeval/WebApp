import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from ifc_extractor.energy import (
    EnergySimulationResult,
    OpenStudioUnavailableError,
)
from model_manager.forms import EpwUploadForm
from model_manager.models import (
    BuildingMetrics,
    CadevilDocument,
    CadevilUser,
    CalculationConfig,
    ConfigUpload,
    EpwUpload,
    FileUpload,
    MaterialProperties,
)
from model_manager.views import (
    compute_best_comparison_stats,
    compute_comparison_stats,
)


EPW_HEADER = b"LOCATION,Vienna,Austria,AUT,TMYx,110350,48.20,16.37,1.0,198.0\n"


class EpwUploadFormTests(TestCase):
    def test_accepts_epw_with_location_header_and_rewinds_file(self) -> None:
        upload = SimpleUploadedFile("vienna.epw", EPW_HEADER + b"weather data")
        form = EpwUploadForm(
            data={"description": "Vienna"}, files={"document": upload}
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["document"].tell(), 0)

    def test_rejects_file_without_location_header(self) -> None:
        form = EpwUploadForm(
            data={"description": "Not weather"},
            files={"document": SimpleUploadedFile("invalid.epw", b"not an EPW")},
        )

        self.assertFalse(form.is_valid())
        self.assertIn("LOCATION header", form.errors["document"][0])

    def test_rejects_non_epw_extension(self) -> None:
        form = EpwUploadForm(
            data={"description": "Wrong extension"},
            files={"document": SimpleUploadedFile("weather.txt", EPW_HEADER)},
        )

        self.assertFalse(form.is_valid())
        self.assertIn("File extension", form.errors["document"][0])


class EpwLibraryAccessTests(TestCase):
    def setUp(self) -> None:
        self.media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

        self.user = CadevilUser.objects.create_user(
            username="weather-owner", password="password"
        )
        self.other_user = CadevilUser.objects.create_user(
            username="other-weather-owner", password="password"
        )
        self.own_epw = EpwUpload.objects.create(
            user=self.user,
            description="Own climate",
            document=SimpleUploadedFile("own.epw", EPW_HEADER),
        )
        self.other_epw = EpwUpload.objects.create(
            user=self.other_user,
            description="Other private climate",
            document=SimpleUploadedFile("other.epw", EPW_HEADER),
        )
        self.client.force_login(self.user)

    def test_profile_uses_islands_and_only_lists_owned_weather(self) -> None:
        response = self.client.get(reverse("user"), HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        for heading in (
            "Account",
            "Material configuration",
            "Weather file library",
            "Calculation capacity",
        ):
            self.assertContains(response, heading)
        self.assertContains(response, "Own climate")
        self.assertNotContains(response, "Other private climate")

    def test_valid_upload_is_owned_by_request_user(self) -> None:
        response = self.client.post(
            reverse("upload_epw"),
            {
                "description": "New climate",
                "document": SimpleUploadedFile("new.epw", EPW_HEADER),
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        upload = EpwUpload.objects.get(description="New climate")
        self.assertEqual(upload.user, self.user)
        self.assertContains(response, "New climate")
        self.assertNotContains(response, "Other private climate")

    def test_invalid_upload_returns_form_errors_without_saving(self) -> None:
        response = self.client.post(
            reverse("upload_epw"),
            {
                "description": "Invalid",
                "document": SimpleUploadedFile("invalid.epw", b"invalid"),
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(EpwUpload.objects.filter(description="Invalid").exists())
        self.assertContains(response, "LOCATION header", status_code=400)

    def test_owner_can_delete_weather_and_file(self) -> None:
        stored_path = self.own_epw.document.path

        response = self.client.post(
            reverse("delete_epw", kwargs={"pk": self.own_epw.pk}),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(EpwUpload.objects.filter(pk=self.own_epw.pk).exists())
        self.assertFalse(Path(stored_path).exists())

    def test_user_cannot_delete_another_users_weather(self) -> None:
        response = self.client.post(
            reverse("delete_epw", kwargs={"pk": self.other_epw.pk})
        )

        self.assertEqual(response.status_code, 404)
        self.assertTrue(EpwUpload.objects.filter(pk=self.other_epw.pk).exists())

    def test_weather_mutations_are_post_only(self) -> None:
        self.assertEqual(self.client.get(reverse("upload_epw")).status_code, 405)
        self.assertEqual(
            self.client.get(
                reverse("delete_epw", kwargs={"pk": self.own_epw.pk})
            ).status_code,
            405,
        )

    def test_weather_library_requires_authentication(self) -> None:
        self.client.logout()

        response = self.client.post(
            reverse("upload_epw"),
            {
                "description": "Anonymous",
                "document": SimpleUploadedFile("anonymous.epw", EPW_HEADER),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(EpwUpload.objects.filter(description="Anonymous").exists())


class ModelComparisonBuildingPerformanceTests(TestCase):
    def setUp(self) -> None:
        self.media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

        self.user = CadevilUser.objects.create_user(username="compare-user")
        self.group = Group.objects.create(name="compare-group")
        upload = FileUpload.objects.create(
            user=self.user, document=ContentFile(b"ifc", name="compare.ifc")
        )
        self.document = CadevilDocument.objects.create(
            user=self.user, group=self.group, upload=upload
        )

    def test_comparison_stats_include_intensities_and_circularity_shares(self) -> None:
        BuildingMetrics.objects.create(
            project=self.document, brutto_grundfläche=100.0
        )
        MaterialProperties.objects.create(
            project=self.document,
            name="Concrete",
            mass=1000.0,
            gwp_ml_a1_a3=200.0,
            ap_ml_a1_a3=10.0,
            penrt_ml_a1_a3=300.0,
            recyclable_mass=750.0,
            waste_mass=250.0,
        )

        stats = compute_comparison_stats(self.document)

        self.assertEqual(stats["gwp_a1_a3_per_bgf"], 2.0)
        self.assertEqual(stats["ap_a1_a3_per_bgf"], 0.1)
        self.assertEqual(stats["penrt_a1_a3_per_bgf"], 3.0)
        self.assertEqual(stats["recyclable_mass_share"], 75.0)
        self.assertEqual(stats["waste_mass_share"], 25.0)

    def test_missing_stats_are_not_selected_as_best(self) -> None:
        unavailable = compute_comparison_stats(self.document)
        available = {
            "gwp_a1_a3_per_bgf": 1.0,
            "ap_a1_a3_per_bgf": 2.0,
            "penrt_a1_a3_per_bgf": 3.0,
            "recyclable_mass_share": 80.0,
            "waste_mass_share": 20.0,
        }

        best = compute_best_comparison_stats([unavailable, available])

        self.assertEqual(best, available)


class BuildingPerformanceTests(APITestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

        self.user = CadevilUser.objects.create_user(username="testuser", password="password")
        self.other_user = CadevilUser.objects.create_user(username="otheruser", password="password")
        self.group = Group.objects.create(name="testgroup")
        self.user.groups.add(self.group)
        
        self.client.login(username="testuser", password="password")
        
        # Create CalculationConfig
        self.config_file = ConfigUpload.objects.create(
            user=self.user,
            document=ContentFile(b"{}", name="config.json"),
            description="Test Config"
        )
        self.config = CalculationConfig.objects.create(
            user=self.user,
            config={"data": {"Concrete": {"Nutzungsdauer": 50, "impact": 1.0}}, "header": {}},
            upload=self.config_file
        )
        
        self.ifc_file = FileUpload.objects.create(
            user=self.user,
            document=ContentFile(b"fake ifc", name="test.ifc"),
            description="Test IFC"
        )
        self.doc = CadevilDocument.objects.create(
            user=self.user,
            group=self.group,
            upload=self.ifc_file,
            description="Test Document"
        )
        self.metrics = BuildingMetrics.objects.create(
            project=self.doc,
            brutto_grundfläche=100.0,
            stockwerke=2.0
        )
        # Create MaterialProperties to satisfy chart_plotter
        MaterialProperties.objects.create(
            project=self.doc,
            name="Concrete",
            volume=10.0
        )
        
        self.epw = EpwUpload.objects.create(
            user=self.user,
            document=ContentFile(b"fake epw", name="test.epw"),
            description="Test EPW"
        )

    def test_calculate_energy_action_exists(self):
        url = reverse("cadevil_document-calculate_energy", kwargs={"pk": self.doc.pk})
        with mock.patch("ifc_extractor.energy.run_energy_simulation") as mock_run:
            mock_run.return_value = EnergySimulationResult(
                annual_total_site_energy_kwh=1234.5,
                annual_electricity_kwh=1000.0,
                annual_natural_gas_kwh=234.5,
                energy_use_intensity_kwh_per_m2=12.3,
                conditioned_floor_area_m2=100.0,
                zone_count=5,
                weather_file_name="test.epw",
                energyplus_version="24.1.0",
                assumptions_summary="Default assumptions",
                simulation_duration_seconds=10.0
            )
            response = self.client.post(url, {"epw_file_id": self.epw.pk})
            self.assertEqual(response.status_code, status.HTTP_302_FOUND)

    def test_object_view_ownership(self):
        self.client.login(username="otheruser", password="password")
        url = reverse("object_view") + f"?object={self.doc.pk}"
        response = self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_object_view_displays_all_metrics(self):
        url = reverse("object_view") + f"?object={self.doc.pk}"
        response = self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, "BGF")
        self.assertContains(response, "BRI")
        self.assertContains(response, "NRF")
        self.assertContains(response, "KGF")
        self.assertContains(response, "Energy rating")
        self.assertContains(response, "Annual site energy")
        self.assertContains(response, "Run energy simulation")

    @mock.patch("ifc_extractor.energy.run_energy_simulation")
    def test_calculate_energy_success(self, mock_run):
        mock_run.return_value = EnergySimulationResult(
            annual_total_site_energy_kwh=1234.5,
            annual_electricity_kwh=1000.0,
            annual_natural_gas_kwh=234.5,
            energy_use_intensity_kwh_per_m2=12.3,
            conditioned_floor_area_m2=100.0,
            zone_count=5,
            weather_file_name="test.epw",
            energyplus_version="24.1.0",
            assumptions_summary="Default assumptions",
            simulation_duration_seconds=10.0
        )
        
        url = reverse("cadevil_document-calculate_energy", kwargs={"pk": self.doc.pk})
        response = self.client.post(url, {"epw_file_id": self.epw.pk}, HTTP_HX_REQUEST="true")
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.metrics.refresh_from_db()
        self.assertEqual(self.metrics.energy_status, "success")
        self.assertEqual(self.metrics.annual_site_energy_kwh, 1234.5)
        
        self.assertContains(response, "Annual site energy")

    def test_calculate_energy_invalid_epw(self):
        other_epw = EpwUpload.objects.create(
            user=self.other_user,
            document=ContentFile(b"fake epw", name="other.epw"),
            description="Other EPW"
        )
        url = reverse("cadevil_document-calculate_energy", kwargs={"pk": self.doc.pk})
        response = self.client.post(url, {"epw_file_id": other_epw.pk})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class ModelCalculationEnergyTests(APITestCase):
    def setUp(self) -> None:
        self.media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

        self.user = CadevilUser.objects.create_user(
            username="calculation-user", password="password"
        )
        self.other_user = CadevilUser.objects.create_user(
            username="other-calculation-user", password="password"
        )
        self.group = Group.objects.create(name="calculation-group")
        self.user.groups.add(self.group)
        config_upload = ConfigUpload.objects.create(
            user=self.user,
            description="Materials",
            document=SimpleUploadedFile("materials.csv", b"configuration"),
        )
        self.config = CalculationConfig.objects.create(
            user=self.user,
            upload=config_upload,
            config={"header": {}, "data": {"Concrete": {}}},
        )
        self.ifc_upload = FileUpload.objects.create(
            user=self.user,
            description="Calculation model",
            document=SimpleUploadedFile("calculation.ifc", b"IFC source"),
        )
        self.epw = EpwUpload.objects.create(
            user=self.user,
            description="Calculation climate",
            document=SimpleUploadedFile("calculation.epw", EPW_HEADER),
        )
        self.other_epw = EpwUpload.objects.create(
            user=self.other_user,
            description="Other climate",
            document=SimpleUploadedFile("other.epw", EPW_HEADER),
        )
        self.glb_path = Path(self.media_directory.name) / "calculation.glb"
        self.glb_path.write_bytes(b"glTF")
        self.client.force_login(self.user)

    def _url(self, upload: FileUpload | None = None) -> str:
        upload = upload or self.ifc_upload
        return f"/api/model_file/{upload.pk}/calculate_model/"

    @staticmethod
    def _walk_result() -> tuple[dict[str, MaterialProperties], BuildingMetrics]:
        return {
            "Concrete": MaterialProperties(
                mass=1000.0, recyclable_mass=700.0, waste_mass=300.0
            )
        }, BuildingMetrics(
            brutto_grundfläche=100.0,
            fassadenflaeche=80.0,
            fassaden_oeffnungsflaeche=20.0,
            fassaden_opake_flaeche=60.0,
            fenster_wand_verhaeltnis=0.25,
        )

    @staticmethod
    def _energy_result() -> EnergySimulationResult:
        return EnergySimulationResult(
            annual_total_site_energy_kwh=5000.0,
            annual_electricity_kwh=4000.0,
            annual_natural_gas_kwh=1000.0,
            energy_use_intensity_kwh_per_m2=50.0,
            conditioned_floor_area_m2=100.0,
            zone_count=2,
            weather_file_name="calculation.epw",
            energyplus_version="25.2.0",
            assumptions_summary="Assumption-based simulation defaults",
            simulation_duration_seconds=1.0,
        )

    @mock.patch("model_manager.views.ifc_to_glb_path")
    @mock.patch("model_manager.views.energy.run_energy_simulation")
    @mock.patch("model_manager.views.helpers.ifc_product_walk")
    def test_calculation_without_weather_skips_energy(
        self, extract_ifc, run_energy, convert_glb
    ) -> None:
        extract_ifc.return_value = self._walk_result()
        convert_glb.return_value = str(self.glb_path)

        response = self.client.post(self._url(), {})

        self.assertEqual(response.status_code, 204)
        run_energy.assert_not_called()
        metrics = BuildingMetrics.objects.get(project__upload=self.ifc_upload)
        self.assertEqual(metrics.energy_status, "not_run")
        self.assertIsNone(metrics.energy_weather_upload)
        self.assertEqual(metrics.fassadenflaeche, 80.0)

    @mock.patch("model_manager.views.ifc_to_glb_path")
    @mock.patch("model_manager.views.energy.run_energy_simulation")
    @mock.patch("model_manager.views.helpers.ifc_product_walk")
    def test_selected_weather_runs_energy_and_persists_all_results(
        self, extract_ifc, run_energy, convert_glb
    ) -> None:
        extract_ifc.return_value = self._walk_result()
        run_energy.return_value = self._energy_result()
        convert_glb.return_value = str(self.glb_path)

        response = self.client.post(
            self._url(), {"epw_file_id": self.epw.pk}
        )

        self.assertEqual(response.status_code, 204)
        run_energy.assert_called_once()
        self.assertEqual(
            run_energy.call_args.args,
            (self.ifc_upload.document.path, self.epw.document.path),
        )
        metrics = BuildingMetrics.objects.get(project__upload=self.ifc_upload)
        self.assertEqual(metrics.energy_status, "success")
        self.assertEqual(metrics.annual_site_energy_kwh, 5000.0)
        self.assertEqual(metrics.annual_electricity_kwh, 4000.0)
        self.assertEqual(metrics.annual_natural_gas_kwh, 1000.0)
        self.assertEqual(metrics.energy_use_intensity_kwh_m2_year, 50.0)
        self.assertEqual(metrics.energy_weather_upload, self.epw)
        self.assertEqual(metrics.openstudio_version, "25.2.0")
        self.assertIsNotNone(metrics.energy_simulated_at)
        self.assertEqual(
            MaterialProperties.objects.get(project=metrics.project).name,
            "Concrete",
        )

    @mock.patch("model_manager.views.ifc_to_glb_path")
    @mock.patch("model_manager.views.energy.run_energy_simulation")
    @mock.patch("model_manager.views.helpers.ifc_product_walk")
    def test_energy_failure_preserves_ifc_results_and_records_error(
        self, extract_ifc, run_energy, convert_glb
    ) -> None:
        extract_ifc.return_value = self._walk_result()
        run_energy.side_effect = OpenStudioUnavailableError("OpenStudio unavailable")
        convert_glb.return_value = str(self.glb_path)

        response = self.client.post(
            self._url(), {"epw_file_id": self.epw.pk}
        )

        self.assertEqual(response.status_code, 204)
        metrics = BuildingMetrics.objects.get(project__upload=self.ifc_upload)
        self.assertEqual(metrics.energy_status, "failed")
        self.assertIn("OpenStudio unavailable", metrics.energy_error)
        self.assertIsNone(metrics.annual_site_energy_kwh)
        self.assertEqual(metrics.fassaden_opake_flaeche, 60.0)
        self.assertEqual(metrics.energy_weather_upload, self.epw)
        self.assertTrue(
            MaterialProperties.objects.filter(project=metrics.project).exists()
        )

    @mock.patch("model_manager.views.helpers.ifc_product_walk")
    def test_foreign_weather_is_rejected_before_extraction(self, extract_ifc) -> None:
        response = self.client.post(
            self._url(), {"epw_file_id": self.other_epw.pk}
        )

        self.assertEqual(response.status_code, 404)
        extract_ifc.assert_not_called()

    @mock.patch("model_manager.views.helpers.ifc_product_walk")
    def test_malformed_weather_id_is_rejected_before_extraction(
        self, extract_ifc
    ) -> None:
        response = self.client.post(
            self._url(), {"epw_file_id": "not-a-uuid"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"valid UUID", response.content)
        extract_ifc.assert_not_called()

    @mock.patch("model_manager.views.helpers.ifc_product_walk")
    def test_missing_material_configuration_is_actionable(self, extract_ifc) -> None:
        self.config.delete()

        response = self.client.post(self._url(), {})

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Select a material configuration", response.content)
        extract_ifc.assert_not_called()

    @mock.patch("model_manager.views.helpers.ifc_product_walk")
    def test_other_users_ifc_upload_is_not_accessible(self, extract_ifc) -> None:
        foreign_upload = FileUpload.objects.create(
            user=self.other_user,
            document=SimpleUploadedFile("private.ifc", b"private"),
        )

        response = self.client.post(self._url(foreign_upload), {})

        self.assertEqual(response.status_code, 404)
        extract_ifc.assert_not_called()
