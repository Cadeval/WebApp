from django.contrib import admin
from django import forms

from plugin_manager.models import PluginRecord


class PluginRecordAdminForm(forms.ModelForm):
    class Meta:
        model = PluginRecord
        fields = "__all__"

    def clean_enabled(self):
        enabled = self.cleaned_data["enabled"]
        if enabled and not self.instance.environment_compatible:
            raise forms.ValidationError("This plugin is unavailable in the current environment.")
        if enabled and self.instance.has_error:
            raise forms.ValidationError("Resolve the discovery error before enabling this plugin.")
        return enabled


@admin.register(PluginRecord)
class PluginRecordAdmin(admin.ModelAdmin):
    form = PluginRecordAdminForm
    list_display = [
        "plugin_id",
        "name",
        "source",
        "artifact_type",
        "compatibility",
        "version",
        "api_version",
        "priority",
        "enabled",
        "has_error_display",
    ]
    list_filter = ["source", "artifact_type", "compatibility", "enabled"]
    search_fields = ["plugin_id", "name"]
    readonly_fields = [
        "plugin_id",
        "name",
        "version",
        "api_version",
        "priority",
        "source",
        "compatibility",
        "artifact_type",
        "artifact",
        "content_hash",
        "package_manifest",
        "signing_key",
        "uploaded_by",
        "uploaded_at",
        "error",
        "discovered_at",
    ]

    @admin.display(boolean=True, description="Error")
    def has_error_display(self, obj: PluginRecord) -> bool:
        return obj.has_error
