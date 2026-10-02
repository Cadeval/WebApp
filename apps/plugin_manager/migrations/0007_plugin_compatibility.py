from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("plugin_manager", "0006_archive_only_uploads")]
    operations = [migrations.AddField(model_name="pluginrecord", name="compatibility", field=models.CharField(choices=[("debug", "Debug only"), ("production", "Production only"), ("both", "Debug and production")], default="both", max_length=16))]
