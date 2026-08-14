from django.contrib import admin

from plugin_manager.models import PluginRecord


@admin.register(PluginRecord)
class PluginRecordAdmin(admin.ModelAdmin):
    list_display = [
        "plugin_id",
        "name",
        "source",
        "artifact_type",
        "version",
        "api_version",
        "priority",
        "enabled",
        "has_error_display",
    ]
    list_filter = ["source", "artifact_type", "enabled"]
    search_fields = ["plugin_id", "name"]
    readonly_fields = [
        "plugin_id",
        "name",
        "version",
        "api_version",
        "priority",
        "source",
        "artifact_type",
        "artifact",
        "content_hash",
        "uploaded_by",
        "uploaded_at",
        "error",
        "discovered_at",
    ]

    @admin.display(boolean=True, description="Error")
    def has_error_display(self, obj: PluginRecord) -> bool:
        return obj.has_error
