from typing import Any
import logging

logger = logging.getLogger(__name__)

from django.http import HttpRequest
from django.templatetags.static import static
from django.urls import reverse

from plugin_manager.models import PluginRecord
from .workflows import selected_plugin_ids, selectable_plugin
from plugin_manager.registry import (
    EDITOR_PLUGIN_EXTENSION_POINT,
    NAV_ITEM_EXTENSION_POINT,
    NavItem,
    registry,
)


def plugin_nav_items(request: HttpRequest) -> dict[str, Any]:
    """Expose only this user's selected and currently available workflow links."""
    try:
        registry.refresh_if_changed()
        user = getattr(request, "user", None)
        enabled_ids = selected_plugin_ids(user)
        nav_items = registry.get_active(NAV_ITEM_EXTENSION_POINT, enabled_ids)
        for record in PluginRecord.objects.filter(source=PluginRecord.Source.UPLOAD, plugin_id__in=enabled_ids).select_related('signing_key'):
            if selectable_plugin(record):
                nav_items.append(NavItem(
                    label=record.name or record.plugin_id,
                    url=reverse('plugin_manager:plugin_workflow', args=[record.plugin_id]),
                    icon='fa-puzzle-piece', priority=record.priority,
                ))
        nav_items.sort(key=lambda item: item.priority)
    except Exception:  # noqa: BLE001 - navigation contributions must never break page rendering
        nav_items = []
    return {"plugin_nav_items": nav_items}


def plugin_editor_items(request: HttpRequest) -> dict[str, Any]:
    """Expose active, declarative editor contributions without executing plugin code."""
    try:
        registry.refresh_if_changed()
        editor_items = registry.get_active(EDITOR_PLUGIN_EXTENSION_POINT, selected_plugin_ids(getattr(request, "user", None)))
    except Exception:  # noqa: BLE001 - isolate package discovery failures
        logger.exception("Could not load package editor contributions")
        editor_items = []
    try:
        editor_items.extend(_uploaded_editor_items(getattr(request, "user", None)))
    except Exception:  # noqa: BLE001 - isolate uploaded artifact discovery failures
        logger.exception("Could not load uploaded editor contributions")
    return {"plugin_editor_items": sorted(editor_items, key=lambda item: item.priority)}


def _uploaded_editor_items(user=None) -> list[Any]:
    from plugin_manager.registry import EditorPlugin

    records = PluginRecord.objects.filter(
        plugin_id__in=selected_plugin_ids(user),
        source=PluginRecord.Source.UPLOAD,
        enabled=True,
        error="",
        artifact_type=PluginRecord.ArtifactType.ZIP,
    ).select_related("signing_key").exclude(artifact="")
    items = []
    for record in records:
        if not record.environment_compatible:
            continue
        from .certificate_authority import trusted_key
        if not trusted_key(record.signing_key):
            continue
        manifest = record.package_manifest
        entry = manifest.get("entrypoint")
        if not entry or entry not in manifest.get("files", {}):
            continue
        artifact_url = reverse("plugin_manager:plugin_asset", kwargs={"plugin_id": record.plugin_id, "asset_path": entry})
        is_wasm = manifest.get("type") == "wasm"
        items.append(
            EditorPlugin(
                id=record.plugin_id,
                name=record.name or record.plugin_id,
                description=record.package_manifest.get("description") or "Reviewed plugin running in a background worker.",
                worker_url=(
                    static("js/plugins/wasm_plugin_worker.js") + "?v=20261002-plugins"
                    if is_wasm
                    else artifact_url
                ),
                wasm_url=artifact_url if is_wasm else "",
                artifact_type=record.artifact_type,
                content_hash=record.content_hash,
                priority=record.priority,
            )
        )
    return items
