from django_bolt import serializers

from apps.plugin_manager.models import PluginRecord


class PluginRecordSerializer(serializers.Serializer):
    class Meta:
        model = PluginRecord
        fields = [
            "plugin_id",
            "name",
            "version",
            "api_version",
            "priority",
            "enabled",
            "error",
            "source",
            "artifact_type",
            "content_hash",
            "uploaded_at",
            "discovered_at",
        ]
        read_only_fields = fields
