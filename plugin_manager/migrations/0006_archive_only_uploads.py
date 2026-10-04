from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("plugin_manager", "0005_plugin_signing_keys")]
    operations = [
        migrations.AlterField(
            model_name="pluginrecord",
            name="artifact_type",
            field=models.CharField(blank=True, choices=[("", "None"), ("zip", "Browser package ZIP")], default="", max_length=8),
        ),
    ]
