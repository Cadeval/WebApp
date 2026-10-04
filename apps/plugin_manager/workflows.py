"""Resolve user workflow choices independently of site-wide plugin availability.

Developer MCP plugins remain controlled by the administrator/debug launcher;
they do not participate in personal browser workflows.
"""
from .models import PluginRecord

DEVELOPMENT_TOOLS = {"cadevil.mcp.context7", "cadevil.mcp.git", "cadevil.mcp.native", "cadevil.mcp.ui_ux", "cadevil.mcp.code_audit"}


def is_workflow_plugin(record):
    return not (record.source == PluginRecord.Source.PACKAGE and record.plugin_id in DEVELOPMENT_TOOLS)


def selectable_plugin(record):
    if not is_workflow_plugin(record) or not record.effective_enabled:
        return False
    if record.source == PluginRecord.Source.UPLOAD:
        key = record.signing_key
        return bool(record.artifact_type == PluginRecord.ArtifactType.ZIP and record.artifact
                    and key and key.owner_id is not None and key.revoked_at is None)
    return True


def selected_plugin_ids(user):
    if not user or not user.is_authenticated:
        return set()
    records = PluginRecord.objects.filter(user_selections__user=user).select_related("signing_key")
    return {record.plugin_id for record in records if selectable_plugin(record)}


def workflow_plugin_enabled(user, plugin_id):
    if not user or not user.is_authenticated:
        return False
    record = PluginRecord.objects.filter(plugin_id=plugin_id, user_selections__user=user).select_related("signing_key").first()
    return bool(record and selectable_plugin(record))
