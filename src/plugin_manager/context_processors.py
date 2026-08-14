from typing import Any

from django.http import HttpRequest
from django.templatetags.static import static
from django.urls import reverse

from plugin_manager.models import PluginRecord

from plugin_manager.registry import (
    EDITOR_PLUGIN_EXTENSION_POINT,
    NAV_ITEM_EXTENSION_POINT,
    registry,
)


def plugin_nav_items(request: HttpRequest) -> dict[str, Any]:
    """Expose the currently active plugin-contributed navigation items to every template."""
    try:
        nav_items = registry.get_active(NAV_ITEM_EXTENSION_POINT)
    except Exception:  # noqa: BLE001 - navigation contributions must never break page rendering
        nav_items = []
    return {"plugin_nav_items": nav_items}


def plugin_editor_items(request: HttpRequest) -> dict[str, Any]:
    """Expose active, declarative editor contributions without executing plugin code."""
    try:
        editor_items = registry.get_active(EDITOR_PLUGIN_EXTENSION_POINT)
        editor_items.extend(_uploaded_editor_items())
    except Exception:  # noqa: BLE001 - editor contributions must never break page rendering
        editor_items = []
    return {"plugin_editor_items": editor_items}


def _uploaded_editor_items() -> list[Any]:
    from plugin_manager.registry import EditorPlugin

    records = PluginRecord.objects.filter(
        source=PluginRecord.Source.UPLOAD,
        enabled=True,
        error="",
    ).exclude(artifact="")
    items = []
    for record in records:
        artifact_url = reverse("plugin_manager:plugin_artifact", args=[record.plugin_id])
        is_wasm = record.artifact_type == PluginRecord.ArtifactType.WEBASSEMBLY
        items.append(
            EditorPlugin(
                id=record.plugin_id,
                name=record.name or record.plugin_id,
                description="Uploaded plugin running in a restricted background worker.",
                worker_url=(
                    static("js/plugins/wasm_plugin_worker.js") if is_wasm else artifact_url
                ),
                wasm_url=artifact_url if is_wasm else "",
                artifact_type=record.artifact_type,
                content_hash=record.content_hash,
                priority=record.priority,
            )
        )
    return items
