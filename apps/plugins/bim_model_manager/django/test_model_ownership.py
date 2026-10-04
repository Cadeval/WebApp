"""BIM persistence belongs to its Django adapter, independent of host auth."""
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ModelState, ProjectState
from django.db.migrations.writer import MigrationWriter
from django.test import SimpleTestCase, TestCase, override_settings

from apps.shared import models as host_models
from . import models, uploads


ROOT = Path(__file__).resolve().parents[4]
MODEL_NAMES = (
    "ConfigUpload", "EpwUpload", "CalculationConfig", "FileUpload",
    "ModelConversion", "BuildingLocation", "CadevilDocument",
    "BuildingMetrics", "MaterialProperties",
)


class BimModelOwnershipTests(SimpleTestCase):
    def test_bim_registry_tables_and_auth_relations_are_plugin_owned(self):
        config = apps.get_app_config("bim_model_manager")
        self.assertEqual(config.name, "apps.plugins.bim_model_manager.django")
        self.assertEqual(Path(config.path), ROOT / "apps/plugins/bim_model_manager")
        self.assertEqual({model.__name__ for model in config.get_models()}, set(MODEL_NAMES))
        self.assertIs(config.models_module, models)
        for name in MODEL_NAMES:
            with self.subTest(model=name):
                model = getattr(models, name)
                self.assertEqual(model.__module__, "apps.plugins.bim_model_manager.django.models")
                self.assertEqual(model._meta.app_label, "bim_model_manager")
                self.assertEqual(model._meta.db_table, "bim_model_manager_" + name.lower())
                self.assertIs(apps.get_model("bim_model_manager", name), model)
                self.assertFalse(hasattr(host_models, name))
        for name in ("ConfigUpload", "EpwUpload", "CalculationConfig", "FileUpload", "CadevilDocument"):
            self.assertIs(getattr(models, name)._meta.get_field("user").remote_field.model,
                          get_user_model())
        self.assertEqual({model.__name__ for model in apps.get_app_config("shared").get_models()},
                         {"CadevilUser"})
        self.assertNotIn("uploads", {field.name for field in host_models.CadevilUser._meta.get_fields()})

    def test_fresh_initial_migrations_describe_host_and_plugin_independently(self):
        loader = MigrationLoader(None)
        historical = loader.project_state()
        detector = MigrationAutodetector(historical, ProjectState.from_apps(apps))
        self.assertEqual(loader.graph.leaf_nodes("shared"), [("shared", "0001_initial")])
        self.assertEqual(loader.graph.leaf_nodes("bim_model_manager"),
                         [("bim_model_manager", "0001_initial")])
        self.assertEqual({name for label, name in historical.models if label == "shared"},
                         {"cadeviluser"})
        for name in MODEL_NAMES:
            with self.subTest(model=name):
                expected = historical.models[("bim_model_manager", name.lower())]
                current = ModelState.from_model(getattr(models, name))
                self.assertEqual(current.options, expected.options)
                self.assertEqual(current.bases, expected.bases)
                self.assertEqual(set(current.fields), set(expected.fields))
                for field_name in current.fields:
                    with self.subTest(field=field_name):
                        self.assertEqual(
                            detector.deep_deconstruct(current.fields[field_name]),
                            detector.deep_deconstruct(expected.fields[field_name]),
                        )

    def test_file_fields_serialize_only_plugin_upload_and_validator_paths(self):
        for name in ("ConfigUpload", "EpwUpload", "FileUpload"):
            field = getattr(models, name)._meta.get_field("document")
            self.assertIs(field.upload_to, uploads.user_directory_path)
            self.assertEqual(MigrationWriter.serialize(field.upload_to)[0],
                             "apps.plugins.bim_model_manager.django.uploads.user_directory_path")
        source = models.ModelConversion._meta.get_field("source")
        self.assertIs(source.upload_to, uploads.cityjson_source_path)
        self.assertEqual(MigrationWriter.serialize(source.upload_to)[0],
                         "apps.plugins.bim_model_manager.django.uploads.cityjson_source_path")
        for model, validator in (
            (models.ConfigUpload, uploads.validate_config_upload_size),
            (models.FileUpload, uploads.validate_model_upload_size),
        ):
            self.assertIn(validator, model._meta.get_field("document").validators)
            self.assertEqual(MigrationWriter.serialize(validator)[0],
                             "apps.plugins.bim_model_manager.django.uploads." + validator.__name__)
        for name in ("user_directory_path", "cityjson_source_path",
                     "validate_config_upload_size", "validate_model_upload_size"):
            self.assertFalse(hasattr(host_models, name))
        for model in (models.FileUpload, models.CadevilDocument):
            self.assertTrue(admin.site.is_registered(model))

    def test_host_auth_can_start_without_installing_or_importing_a_plugin(self):
        script = """import json, sys
from django.conf import settings
settings.configure(SECRET_KEY='isolated-host-test', ADMIN_LOG_ENABLED=False,
    INSTALLED_APPS=['django.contrib.auth', 'django.contrib.contenttypes', 'apps.shared'],
    AUTH_USER_MODEL='shared.CadevilUser', DEFAULT_AUTO_FIELD='django.db.models.BigAutoField',
    DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}})
import django
django.setup()
from django.apps import apps
print(json.dumps({
    'host_models': [model.__name__ for model in apps.get_app_config('shared').get_models()],
    'plugin_imports': [name for name in sys.modules if name.startswith('apps.plugins.')],
}))
"""
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                                check=True, capture_output=True, text=True, timeout=10)
        self.assertEqual(json.loads(result.stdout), {"host_models": ["CadevilUser"], "plugin_imports": []})

    def test_bim_app_lifecycle_owns_cleanup_registration_after_models_load(self):
        script = """import json, os, sys
os.environ['DJANGO_SETTINGS_MODULE'] = 'tests.passport_test_settings'
from apps.plugins.bim_model_manager.django.apps import BIMConfig
from django.apps import apps
from django.db.models.signals import post_delete
original = BIMConfig.ready
observed = []
def ready(self):
    observed.append({
        'models_ready': apps.models_ready,
        'signals_already_imported': 'apps.plugins.bim_model_manager.django.signals' in sys.modules,
    })
    if sys.argv[1] == 'enabled':
        original(self)
BIMConfig.ready = ready
import django
django.setup()
model = apps.get_model('bim_model_manager', 'ModelConversion')
if sys.argv[1] == 'enabled':
    original(apps.get_app_config('bim_model_manager'))
print(json.dumps({
    'observed': observed,
    'has_cleanup': post_delete.has_listeners(model),
    'signals_imported': 'apps.plugins.bim_model_manager.django.signals' in sys.modules,
}))
"""
        for mode in ("suppressed", "enabled"):
            with self.subTest(app_lifecycle=mode):
                result = subprocess.run([sys.executable, "-c", script, mode], cwd=ROOT,
                                        check=True, capture_output=True, text=True, timeout=10)
                startup = json.loads(result.stdout)
                self.assertEqual(startup["observed"], [{
                    "models_ready": True, "signals_already_imported": False,
                }])
                self.assertEqual(startup["has_cleanup"], mode == "enabled")
                self.assertEqual(startup["signals_imported"], mode == "enabled")

    @override_settings(MODEL_MANAGER_MAX_CONFIG_UPLOAD_SIZE=50, MODEL_MANAGER_MAX_MODEL_UPLOAD_SIZE=100)
    def test_plugin_upload_rules_keep_private_paths_and_size_limits(self):
        instance = SimpleNamespace(user=SimpleNamespace(id=7))
        self.assertEqual(uploads.user_directory_path(instance, "house.ifc"), "user_7/house.ifc")
        conversion = SimpleNamespace(upload=SimpleNamespace(user_id=7), upload_id="model-id")
        self.assertEqual(uploads.cityjson_source_path(conversion, "folder/source.json"),
                         "cityjson/7/model-id/source.json")
        for validator, limit in ((uploads.validate_config_upload_size, 50),
                                 (uploads.validate_model_upload_size, 100)):
            validator(SimpleNamespace(size=limit))
            with self.assertRaises(ValidationError):
                validator(SimpleNamespace(size=limit + 1))


class BimModelDatabaseTests(TestCase):
    def test_fresh_schema_permissions_and_content_types_are_plugin_owned(self):
        tables = set(connection.introspection.table_names())
        for name in MODEL_NAMES:
            with self.subTest(model=name):
                model = getattr(models, name)
                self.assertIn("bim_model_manager_" + name.lower(), tables)
                content_type = ContentType.objects.get_for_model(model)
                self.assertEqual((content_type.app_label, content_type.model),
                                 ("bim_model_manager", name.lower()))
                self.assertTrue(Permission.objects.filter(
                    content_type=content_type, codename="add_" + name.lower()).exists())
        self.assertEqual(set(ContentType.objects.filter(app_label="shared").values_list("model", flat=True)),
                         {"cadeviluser"})
        self.assertFalse({"building_metrics", "material_properties", "shared_modelconversion",
                          "shared_buildinglocation", "library_cadeviluser_uploads"} & tables)
        self.assertFalse(any(table.startswith("archicad_eval_") for table in tables))

    def test_cityjson_source_cleanup_remains_single_and_after_commit(self):
        user = get_user_model().objects.create_user(username="isolated-bim-owner")
        self.assertEqual(list(user.groups.values_list("name", flat=True)), ["user_isolated-bim-owner"])
        upload = models.FileUpload.objects.create(user=user, document="owned/house.ifc")
        conversion = models.ModelConversion.objects.create(
            upload=upload, source="cityjson/owned/source.json", source_sha256="a" * 64,
            output_sha256="b" * 64, metadata={})
        with patch.object(conversion.source.storage, "delete") as delete:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                conversion.delete()
                delete.assert_not_called()
            self.assertEqual(len(callbacks), 1)
            delete.assert_called_once_with("cityjson/owned/source.json")
        self.assertFalse(models.ModelConversion.objects.filter(upload=upload).exists())
