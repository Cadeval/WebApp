from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies=[("plugin_manager","0004_plugin_packages"),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[
        migrations.CreateModel(name="PluginSigningKey",fields=[
            ("id",models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name="ID")),
            ("label",models.CharField(max_length=120)),("fingerprint",models.CharField(max_length=64,unique=True)),
            ("public_key",models.CharField(max_length=44)),("created_at",models.DateTimeField(auto_now_add=True)),
            ("revoked_at",models.DateTimeField(blank=True,null=True)),
            ("owner",models.ForeignKey(null=True,on_delete=django.db.models.deletion.SET_NULL,related_name="plugin_signing_keys",to=settings.AUTH_USER_MODEL)),
        ],options={"ordering":["-created_at"]}),
        migrations.AddField(model_name="pluginrecord",name="signing_key",field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.PROTECT,related_name="plugins",to="plugin_manager.pluginsigningkey")),
    ]
