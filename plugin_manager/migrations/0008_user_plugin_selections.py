from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("plugin_manager", "0007_plugin_compatibility"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [migrations.CreateModel(
        name="UserPluginSelection",
        fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("plugin", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                        related_name="user_selections", to="plugin_manager.pluginrecord")),
            ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                      related_name="plugin_selections", to=settings.AUTH_USER_MODEL)),
        ],
        options={"constraints": [models.UniqueConstraint(fields=("user", "plugin"),
                                                         name="unique_user_plugin_selection")]},
    )]
