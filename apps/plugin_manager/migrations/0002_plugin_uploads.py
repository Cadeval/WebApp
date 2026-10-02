from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("plugin_manager", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="pluginrecord",
            name="artifact",
            field=models.FileField(blank=True, upload_to="plugins/"),
        ),
        migrations.AddField(
            model_name="pluginrecord",
            name="artifact_type",
            field=models.CharField(
                blank=True,
                choices=[("", "None"), ("js", "JavaScript"), ("wasm", "WebAssembly")],
                default="",
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name="pluginrecord",
            name="content_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="pluginrecord",
            name="source",
            field=models.CharField(
                choices=[("package", "Installed package"), ("upload", "Uploaded file")],
                default="package",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="pluginrecord",
            name="uploaded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pluginrecord",
            name="uploaded_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="uploaded_plugins",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]