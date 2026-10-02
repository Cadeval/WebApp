import os
import uuid

import django.core.validators
import django.db.models.deletion
import model_manager.models
from django.db import migrations, models


def move_weather_files_to_user_library(apps, schema_editor) -> None:
    FileUpload = apps.get_model("model_manager", "FileUpload")
    EpwUpload = apps.get_model("model_manager", "EpwUpload")

    for upload in FileUpload.objects.exclude(weather_file__isnull=True).exclude(
        weather_file=""
    ):
        weather_name = str(upload.weather_file)
        EpwUpload.objects.get_or_create(
            user_id=upload.user_id,
            document=weather_name,
            defaults={"description": os.path.basename(weather_name)},
        )


class Migration(migrations.Migration):
    dependencies = [
        (
            "model_manager",
            "0008_buildingmetrics_annual_electricity_kwh_and_more",
        ),
    ]

    operations = [
        migrations.CreateModel(
            name="EpwUpload",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        db_index=True,
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "document",
                    models.FileField(
                        upload_to=model_manager.models.user_directory_path,
                        validators=[
                            django.core.validators.FileExtensionValidator(
                                allowed_extensions=("epw",)
                            )
                        ],
                    ),
                ),
                (
                    "description",
                    models.CharField(blank=True, db_index=True, max_length=255),
                ),
                (
                    "uploaded_at",
                    models.DateTimeField(auto_now_add=True, db_index=True),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="epw_uploads",
                        to="model_manager.cadeviluser",
                    ),
                ),
            ],
            options={
                "db_table": "archicad_eval_epw_uploads",
                "ordering": ("description", "uploaded_at", "id"),
            },
        ),
        migrations.AddField(
            model_name="buildingmetrics",
            name="energy_weather_upload",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="building_metrics",
                to="model_manager.epwupload",
            ),
        ),
        migrations.RunPython(
            move_weather_files_to_user_library,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="fileupload",
            name="weather_file",
        ),
    ]