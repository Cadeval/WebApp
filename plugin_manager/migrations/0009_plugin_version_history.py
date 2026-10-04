from django.db import migrations, models


def preserve_current_versions(apps, schema_editor):
    PluginRecord = apps.get_model("plugin_manager", "PluginRecord")
    for record in PluginRecord.objects.using(schema_editor.connection.alias).iterator():
        if not record.version:
            continue
        observed_at = record.uploaded_at or record.discovered_at
        PluginRecord.objects.using(schema_editor.connection.alias).filter(pk=record.pk).update(
            version_history=[{"version": record.version, "api_version": record.api_version,
                              "source": record.source, "content_hash": record.content_hash,
                              "observed_at": observed_at.isoformat(), "basis": "stored"}]
        )


class Migration(migrations.Migration):
    dependencies = [("plugin_manager", "0008_user_plugin_selections")]
    operations = [
        migrations.AddField(model_name="pluginrecord", name="version_history",
                            field=models.JSONField(blank=True, db_default=[], default=list)),
        migrations.RunPython(preserve_current_versions, migrations.RunPython.noop),
    ]
