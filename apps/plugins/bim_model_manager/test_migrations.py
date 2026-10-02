from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from model_manager.models import (
    BuildingMetrics,
    EpwUpload,
    FileUpload,
    MaterialProperties,
)


class MigrationTestCase(TransactionTestCase):
    """Base class for testing the effects of applying a specific migration.

    Subclasses must set ``migrate_from``/``migrate_to`` to ``(app_label,
    migration_name)`` tuples and can override ``setUpBeforeMigration`` to
    create data using the historical models available at ``migrate_from``.
    """

    migrate_from: list[tuple[str, str]] = []
    migrate_to: list[tuple[str, str]] = []

    def setUp(self) -> None:
        assert self.migrate_from and self.migrate_to, (
            f"TestCase '{type(self).__name__}' must define "
            "migrate_from and migrate_to properties"
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        self.setUpBeforeMigration(old_apps)

        # Reload the executor: the previous one has a stale migration plan
        # after having just applied migrate_from.
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def setUpBeforeMigration(self, apps) -> None:
        pass

    def tearDown(self) -> None:
        # Migrate back to the latest state so that other tests are not
        # affected by leaving the schema at an intermediate migration.
        call_command("migrate", "model_manager", verbosity=0)


class Migration0007PreservesHistoricalDataTests(MigrationTestCase):
    """Regression test for the 0007 rename/add-field migration.

    Historically ``ap_ml``/``gwp_ml``/``penrt_ml`` must end up in
    ``ap_ml_a1_a3``/``gwp_ml_a1_a3``/``penrt_ml_a1_a3`` respectively, while the
    new B4/lifecycle fields must default to zero for pre-existing rows.
    """

    migrate_from = [("model_manager", "0006_alter_configupload_user")]
    migrate_to = [
        (
            "model_manager",
            "0007_rename_ap_ml_materialproperties_ap_ml_a1_a3_and_more",
        )
    ]

    AP_ML = 12.5
    GWP_ML = 345.75
    PENRT_ML = 9876.5

    def setUpBeforeMigration(self, apps) -> None:
        HistoricalGroup = apps.get_model("auth", "Group")
        HistoricalUser = apps.get_model("model_manager", "CadevilUser")
        HistoricalFileUpload = apps.get_model("model_manager", "FileUpload")
        HistoricalDocument = apps.get_model("model_manager", "CadevilDocument")
        HistoricalMaterialProperties = apps.get_model(
            "model_manager", "MaterialProperties"
        )

        group = HistoricalGroup.objects.create(name="historical-group")
        user = HistoricalUser.objects.create(username="historical-user")
        upload = HistoricalFileUpload.objects.create(
            user=user, document="legacy.ifc", description="Legacy"
        )
        document = HistoricalDocument.objects.create(
            user=user, group=group, upload=upload, description="Legacy Model"
        )

        self.material_pk = HistoricalMaterialProperties.objects.create(
            project_id=document.pk,
            name="Legacy Material",
            ap_ml=self.AP_ML,
            gwp_ml=self.GWP_ML,
            penrt_ml=self.PENRT_ML,
        ).pk

    def test_legacy_metrics_are_renamed_to_matching_a1_a3_fields(self) -> None:
        material = MaterialProperties.objects.get(pk=self.material_pk)

        self.assertEqual(material.ap_ml_a1_a3, self.AP_ML)
        self.assertEqual(material.gwp_ml_a1_a3, self.GWP_ML)
        self.assertEqual(material.penrt_ml_a1_a3, self.PENRT_ML)

    def test_new_b4_and_lifecycle_fields_default_to_zero(self) -> None:
        material = MaterialProperties.objects.get(pk=self.material_pk)

        self.assertEqual(material.ap_ml_a1_a3_b4, 0.0)
        self.assertEqual(material.gwp_ml_a1_a3_b4, 0.0)
        self.assertEqual(material.penrt_ml_a1_a3_b4, 0.0)
        self.assertEqual(material.ap_ml_lz, 0.0)
        self.assertEqual(material.gwp_ml_lz, 0.0)
        self.assertEqual(material.penrt_ml_lz, 0.0)


class Migration0008AddsBuildingPerformanceDefaultsTests(MigrationTestCase):
    migrate_from = [
        (
            "model_manager",
            "0007_rename_ap_ml_materialproperties_ap_ml_a1_a3_and_more",
        )
    ]
    migrate_to = [
        (
            "model_manager",
            "0008_buildingmetrics_annual_electricity_kwh_and_more",
        )
    ]

    def setUpBeforeMigration(self, apps) -> None:
        HistoricalGroup = apps.get_model("auth", "Group")
        HistoricalUser = apps.get_model("model_manager", "CadevilUser")
        HistoricalFileUpload = apps.get_model("model_manager", "FileUpload")
        HistoricalDocument = apps.get_model("model_manager", "CadevilDocument")
        HistoricalBuildingMetrics = apps.get_model(
            "model_manager", "BuildingMetrics"
        )

        group = HistoricalGroup.objects.create(name="performance-history-group")
        user = HistoricalUser.objects.create(username="performance-history-user")
        upload = HistoricalFileUpload.objects.create(
            user=user, document="existing.ifc", description="Existing"
        )
        document = HistoricalDocument.objects.create(
            user=user, group=group, upload=upload, description="Existing Model"
        )
        self.upload_pk = upload.pk
        self.metrics_pk = HistoricalBuildingMetrics.objects.create(
            project=document,
            fassadenflaeche=123.5,
            brutto_grundfläche=456.25,
        ).pk

    def test_existing_metrics_are_preserved_with_safe_new_defaults(self) -> None:
        HistoricalBuildingMetrics = self.apps.get_model(
            "model_manager", "BuildingMetrics"
        )
        metrics = HistoricalBuildingMetrics.objects.get(pk=self.metrics_pk)

        self.assertEqual(metrics.fassadenflaeche, 123.5)
        self.assertEqual(metrics.brutto_grundfläche, 456.25)
        self.assertEqual(metrics.fassaden_oeffnungsflaeche, 0.0)
        self.assertEqual(metrics.fassaden_opake_flaeche, 0.0)
        self.assertEqual(metrics.fenster_wand_verhaeltnis, 0.0)
        self.assertEqual(metrics.energy_status, "not_run")
        self.assertIsNone(metrics.annual_site_energy_kwh)
        self.assertIsNone(metrics.energy_use_intensity_kwh_m2_year)

    def test_existing_uploads_receive_an_optional_legacy_weather_field(self) -> None:
        HistoricalFileUpload = self.apps.get_model("model_manager", "FileUpload")
        upload = HistoricalFileUpload.objects.get(pk=self.upload_pk)

        self.assertEqual(upload.document.name, "existing.ifc")
        self.assertFalse(upload.weather_file)


class Migration0009MovesWeatherFilesToUserLibraryTests(MigrationTestCase):
    migrate_from = [
        (
            "model_manager",
            "0008_buildingmetrics_annual_electricity_kwh_and_more",
        )
    ]
    migrate_to = [
        ("model_manager", "0009_move_epw_uploads_to_user_library")
    ]

    def setUpBeforeMigration(self, apps) -> None:
        HistoricalGroup = apps.get_model("auth", "Group")
        HistoricalUser = apps.get_model("model_manager", "CadevilUser")
        HistoricalFileUpload = apps.get_model("model_manager", "FileUpload")
        HistoricalDocument = apps.get_model("model_manager", "CadevilDocument")
        HistoricalBuildingMetrics = apps.get_model(
            "model_manager", "BuildingMetrics"
        )

        group = HistoricalGroup.objects.create(name="weather-history-group")
        user = HistoricalUser.objects.create(username="weather-history-user")
        upload = HistoricalFileUpload.objects.create(
            user=user,
            document="user_legacy/existing.ifc",
            weather_file="user_legacy/vienna.epw",
            description="Existing",
        )
        document = HistoricalDocument.objects.create(
            user=user, group=group, upload=upload, description="Existing Model"
        )
        self.user_pk = user.pk
        self.upload_pk = upload.pk
        self.metrics_pk = HistoricalBuildingMetrics.objects.create(
            project=document,
            fassadenflaeche=42.0,
        ).pk

    def test_legacy_weather_file_is_preserved_in_personal_library(self) -> None:
        epw_upload = EpwUpload.objects.get(user_id=self.user_pk)

        self.assertEqual(epw_upload.document.name, "user_legacy/vienna.epw")
        self.assertEqual(epw_upload.description, "vienna.epw")

    def test_obsolete_per_ifc_weather_field_is_removed(self) -> None:
        upload = FileUpload.objects.get(pk=self.upload_pk)

        self.assertEqual(upload.document.name, "user_legacy/existing.ifc")
        self.assertNotIn(
            "weather_file", {field.name for field in upload._meta.get_fields()}
        )

    def test_existing_metrics_remain_intact_and_new_relation_is_optional(self) -> None:
        metrics = BuildingMetrics.objects.get(pk=self.metrics_pk)

        self.assertEqual(metrics.fassadenflaeche, 42.0)
        self.assertIsNone(metrics.energy_weather_upload)
