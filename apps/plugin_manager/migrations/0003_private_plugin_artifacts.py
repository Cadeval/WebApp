from django.db import migrations, models
from apps.plugin_manager.storage import PrivatePluginStorage


class Migration(migrations.Migration):
    dependencies=[("plugin_manager","0002_plugin_uploads")]
    operations=[migrations.AlterField(model_name="pluginrecord",name="artifact",field=models.FileField(blank=True,upload_to="plugins/",storage=PrivatePluginStorage()))]
