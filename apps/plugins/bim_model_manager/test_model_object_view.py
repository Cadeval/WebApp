import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from ifc_extractor.energy import (
    EnergySimulationResult,
    OpenStudioUnavailableError,
)
from model_manager.models import (
    BuildingMetrics,
    CadevilDocument,
    CalculationConfig,
    ConfigUpload,
    EpwUpload,
    FileUpload,
    MaterialProperties,
)

User = get_user_model()


class ModelObjectViewTests(TestCase):
    def setUp(self) -> None:
        self.media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

        self.user = User.objects.create_user(
            username="model-owner", password="pw12345678"
        )
        self.other_user = User.objects.create_user(
            username="other-owner", password="pw12345678"
        )
        self.group = Group.objects.create(name="model-owner-group")
        self.user.groups.add(self.group)
        self.other_group = Group.objects.create(name="other-owner-group")
        self.other_user.groups.add(self.other_group)

        self.upload = FileUpload.objects.create(
            user=self.user,
            description="Office source",
            document=SimpleUploadedFile("office.ifc", b"IFC source"),
        )
        self.document = CadevilDocument.objects.create(
            user=self.user,
            group=self.group,
            upload=self.upload,
            description="Office model",
        )
        self.metrics = BuildingMetrics.objects.create(
            project=self.document,
            grundstuecksfläche=1000.0,
            bebaute_fläche=350.0,
            unbebaute_fläche=650.0,
            brutto_rauminhalt=4800.0,
            brutto_grundfläche=1200.0,
            konstruktions_grundfläche=220.0,
            netto_raumfläche=980.0,
            bgf_bf_ratio=3.43,
            bri_bgf_ratio=4.0,
            fassadenflaeche=900.0,
            fassaden_oeffnungsflaeche=180.0,
            fassaden_opake_flaeche=720.0,
            fenster_wand_verhaeltnis=0.2,
            stockwerke=4.0,
            energie_bewertung="Assumption based",
        )
        self.config_upload = ConfigUpload.objects.create(
            user=self.user,
            description="Lifecycle configuration",
            document=SimpleUploadedFile("materials.csv", b"configuration"),
        )
        self.calculation_config = CalculationConfig.objects.create(
            user=self.user,
            upload=self.config_upload,
            config={
                "header": {},
                "data": {
                    "Concrete": {
                        "Nutzungsdauer": 80,
                        "Abfallreduktion": 20,
                        "Neu Abfallreduktion": 10,
                        "Recycling": 60,
                        "Neu Recycling": 70,
                    },
                    "Steel": {
                        "Nutzungsdauer": 50,
                        "Abfallreduktion": 5,
                        "Neu Abfallreduktion": 2,
                        "Recycling": 90,
                        "Neu Recycling": 95,
                    },
                },
            },
        )
        self.concrete = MaterialProperties.objects.create(
            project=self.document,
            name="Concrete",
            global_brutto_price=1.0,
            local_brutto_price=2.0,
            local_netto_price=3.0,
            volume=40.0,
            area=50.0,
            length=60.0,
            mass=5000.0,
            penrt_ml_a1_a3=7.0,
            gwp_ml_a1_a3=8.0,
            ap_ml_a1_a3=9.0,
            penrt_ml_a1_a3_b4=10.0,
            gwp_ml_a1_a3_b4=11.0,
            ap_ml_a1_a3_b4=12.0,
            penrt_ml_lz=13.0,
            gwp_ml_lz=14.0,
            ap_ml_lz=15.0,
            recyclable_mass=3000.0,
            waste_mass=2000.0,
        )
        self.steel = MaterialProperties.objects.create(
            project=self.document,
            name="Steel",
            mass=1000.0,
            recyclable_mass=900.0,
            waste_mass=100.0,
        )
        self.epw = EpwUpload.objects.create(
            user=self.user,
            description="Vienna climate",
            document=SimpleUploadedFile("vienna.epw", b"weather"),
        )
        self.other_epw = EpwUpload.objects.create(
            user=self.other_user,
            description="Private climate",
            document=SimpleUploadedFile("private.epw", b"private weather"),
        )

        self.client.force_login(self.user)
        self.chart_patchers = [
            patch(
                "model_manager.views.chart_plotter.plot_mass",
                return_value='<div id="mass-chart">Mass chart</div>',
            ),
            patch(
                "model_manager.views.chart_plotter.plot_material_waste_grades",
                return_value='<div id="waste-chart">Waste chart</div>',
            ),
            patch(
                "model_manager.views.chart_plotter.create_onorm_1800_visualization",
                return_value='<div id="onorm-chart">ONORM chart</div>',
            ),
            patch(
                "model_manager.views.chart_plotter.plot_material_costs",
                return_value='<div id="cost-chart">Cost chart</div>',
            ),
        ]
        for patcher in self.chart_patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _detail_url(self, **query) -> str:
        parameters = {"object": self.document.pk, **query}
        return reverse("object_view") + "?" + "&".join(
            f"{key}={value}" for key, value in parameters.items()
        )

    def _energy_result(self) -> EnergySimulationResult:
        return EnergySimulationResult(
            annual_total_site_energy_kwh=12345.0,
            annual_electricity_kwh=9000.0,
            annual_natural_gas_kwh=3345.0,
            energy_use_intensity_kwh_per_m2=12.6,
            conditioned_floor_area_m2=980.0,
            zone_count=4,
            weather_file_name="vienna.epw",
            energyplus_version="25.2.0",
            assumptions_summary="Assumption-based simulation defaults",
            simulation_duration_seconds=2.5,
        )

    def test_detail_lists_all_domain_property_sections_and_material_fields(self) -> None:
        response = self.client.get(self._detail_url(), HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        for label in (
            "Model overview",
            "Spatial metrics",
            "Facade metrics",
            "Energy performance",
            "Material properties",
            "Source description",
            "Metrics ID",
            "Property area (GF)",
            "Built-up area (BF)",
            "Unbuilt area (UF)",
            "Gross volume (BRI)",
            "Gross floor area (BGF)",
            "Construction area (KGF)",
            "Net floor area (NRF)",
            "BGF/BF ratio",
            "BRI/BGF ratio",
            "Global gross price",
            "PEnrT A1–A3 + B4",
            "Recyclable mass",
            "Waste mass",
        ):
            self.assertContains(response, label)
        self.assertContains(response, "office.ifc")
        self.assertNotContains(response, str(self.upload.document.path))

    def test_detail_only_lists_current_users_weather_files(self) -> None:
        response = self.client.get(self._detail_url(), HTTP_HX_REQUEST="true")

        self.assertContains(response, "Vienna climate")
        self.assertNotContains(response, "Private climate")

    def test_recycling_selector_defaults_to_highest_mass_material(self) -> None:
        response = self.client.get(self._detail_url(), HTTP_HX_REQUEST="true")

        self.assertContains(response, 'name="material_id"')
        self.assertContains(response, 'hx-trigger="change"')
        self.assertContains(response, 'hx-get="')
        self.assertContains(response, f'value="{self.concrete.pk}" selected')
        self.assertContains(response, "Material Recycling Simulation: Concrete")
        self.assertContains(response, f'value="{self.steel.pk}"')

    def test_recycling_selector_can_render_a_different_material(self) -> None:
        response = self.client.get(
            self._detail_url(material_id=self.steel.pk), HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'value="{self.steel.pk}" selected')
        self.assertContains(response, "Material Recycling Simulation: Steel")
        self.assertNotContains(response, "Material Recycling Simulation: Concrete")

    def test_cost_projection_defaults_to_highest_mass_material(self) -> None:
        response = self.client.get(self._detail_url(), HTTP_HX_REQUEST="true")

        self.assertContains(response, "Rising Cost Over Time: Concrete")

    def test_cost_projection_can_render_a_different_material(self) -> None:
        response = self.client.get(
            self._detail_url(material_id=self.steel.pk), HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rising Cost Over Time: Steel")
        self.assertNotContains(response, "Rising Cost Over Time: Concrete")

    def test_detail_includes_material_cost_chart(self) -> None:
        response = self.client.get(self._detail_url(), HTTP_HX_REQUEST="true")

        self.assertContains(response, "Cost chart")

    def test_recycling_partial_rejects_material_from_another_model(self) -> None:
        other_upload = FileUpload.objects.create(
            user=self.user,
            document=SimpleUploadedFile("other.ifc", b"other"),
        )
        other_document = CadevilDocument.objects.create(
            user=self.user,
            group=self.group,
            upload=other_upload,
        )
        foreign_material = MaterialProperties.objects.create(
            project=other_document, name="Foreign", mass=1.0
        )

        response = self.client.get(
            reverse("material_recycling_simulation", kwargs={"pk": self.document.pk}),
            {"material_id": foreign_material.pk},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Select a material from this model", status_code=400)
        self.assertNotContains(response, "Foreign", status_code=400)

    def test_recycling_partial_is_scoped_to_document_owner(self) -> None:
        self.client.force_login(self.other_user)

        response = self.client.get(
            reverse("material_recycling_simulation", kwargs={"pk": self.document.pk})
        )

        self.assertEqual(response.status_code, 404)

    def test_detail_handles_no_materials_or_configuration(self) -> None:
        self.document.material_properties.all().delete()
        self.calculation_config.delete()

        response = self.client.get(self._detail_url(), HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No materials are available for this model")

    def test_detail_handles_missing_building_metrics(self) -> None:
        self.metrics.delete()

        response = self.client.get(self._detail_url(), HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Building metrics are not available")
        self.assertNotContains(response, "None")

    def test_detail_rejects_another_users_document(self) -> None:
        self.client.force_login(self.other_user)

        response = self.client.get(self._detail_url(), HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 404)

    @patch("model_manager.views.helpers.ifc_product_walk")
    @patch("model_manager.views.energy.run_energy_simulation")
    def test_energy_action_runs_only_energy_and_persists_result(
        self, run_simulation, extract_ifc
    ) -> None:
        run_simulation.return_value = self._energy_result()
        material_ids = list(
            self.document.material_properties.order_by("id").values_list("id", flat=True)
        )

        response = self.client.post(
            reverse(
                "cadevil_document-calculate_energy", kwargs={"pk": self.document.pk}
            ),
            {"epw_file_id": self.epw.pk},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        run_simulation.assert_called_once()
        args = run_simulation.call_args.args
        self.assertEqual(args, (self.upload.document.path, self.epw.document.path))
        extract_ifc.assert_not_called()
        self.metrics.refresh_from_db()
        self.assertEqual(self.metrics.energy_status, "success")
        self.assertEqual(self.metrics.annual_site_energy_kwh, 12345.0)
        self.assertEqual(self.metrics.energy_weather_upload, self.epw)
        self.assertEqual(
            material_ids,
            list(
                self.document.material_properties.order_by("id").values_list(
                    "id", flat=True
                )
            ),
        )
        self.assertContains(response, "Energy performance")
        self.assertNotContains(response, "Model overview")

    @patch("model_manager.views.energy.run_energy_simulation")
    def test_energy_failure_is_persisted_and_rendered(self, run_simulation) -> None:
        run_simulation.side_effect = OpenStudioUnavailableError("OpenStudio unavailable")

        response = self.client.post(
            reverse(
                "cadevil_document-calculate_energy", kwargs={"pk": self.document.pk}
            ),
            {"epw_file_id": self.epw.pk},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.metrics.refresh_from_db()
        self.assertEqual(self.metrics.energy_status, "failed")
        self.assertIn("OpenStudio unavailable", self.metrics.energy_error)
        self.assertContains(response, "OpenStudio unavailable")

    def test_energy_action_validates_weather_and_document_ownership(self) -> None:
        energy_url = reverse(
            "cadevil_document-calculate_energy", kwargs={"pk": self.document.pk}
        )

        self.assertEqual(self.client.post(energy_url, {}).status_code, 400)
        self.assertEqual(
            self.client.post(
                energy_url, {"epw_file_id": "not-a-uuid"}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                energy_url, {"epw_file_id": self.other_epw.pk}
            ).status_code,
            404,
        )
        self.client.force_login(self.other_user)
        self.assertEqual(
            self.client.post(
                energy_url, {"epw_file_id": self.other_epw.pk}
            ).status_code,
            404,
        )

    def test_energy_action_is_post_only_and_requires_authentication(self) -> None:
        energy_url = reverse(
            "cadevil_document-calculate_energy", kwargs={"pk": self.document.pk}
        )

        self.assertEqual(self.client.get(energy_url).status_code, 405)
        self.client.logout()
        self.assertIn(
            self.client.post(energy_url, {"epw_file_id": self.epw.pk}).status_code,
            (401, 403),
        )

    def test_energy_form_is_accessible_and_disabled_without_weather(self) -> None:
        self.epw.delete()

        response = self.client.get(self._detail_url(), HTTP_HX_REQUEST="true")

        self.assertContains(response, 'for="energy-weather-file"')
        self.assertContains(response, 'id="energy-weather-file"')
        self.assertContains(response, "Run energy simulation")
        self.assertContains(response, "disabled")
