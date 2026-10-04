from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies=[("plugin_manager","0003_private_plugin_artifacts")]
    operations=[
        migrations.AddField(model_name="pluginrecord",name="package_manifest",field=models.JSONField(default=dict,blank=True)),
        migrations.AlterField(model_name="pluginrecord",name="artifact_type",field=models.CharField(max_length=8,blank=True,default="",choices=[("","None"),("js","JavaScript"),("wasm","WebAssembly"),("zip","Browser package ZIP")])),
    ]
