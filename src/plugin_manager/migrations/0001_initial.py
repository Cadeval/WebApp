from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="PluginRecord",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("plugin_id", models.CharField(db_index=True, max_length=255, unique=True)),
                ("name", models.CharField(blank=True, default="", max_length=255)),
                ("version", models.CharField(blank=True, default="", max_length=50)),
                ("api_version", models.CharField(blank=True, default="", max_length=20)),
                ("priority", models.IntegerField(default=100)),
                ("enabled", models.BooleanField(default=True)),
                ("error", models.TextField(blank=True, default="")),
                ("discovered_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "plugin_manager_plugin_record",
                "ordering": ["priority", "plugin_id"],
            },
        ),
    ]
