import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.shared.services import staff_required as staff_required

from apps.plugin_manager.forms import PluginUploadForm
from apps.plugin_manager.models import PluginRecord
from apps.plugin_manager.registry import DiscoveryResult, registry

logger = logging.getLogger("plugin_manager")


def manage_plugin(plugin_id: str, action: str) -> bool:
    """Load or unload a plugin through one audited lifecycle boundary."""
    normalized_action = action.lower()
    if normalized_action in {"load", "enable"}:
        enabled = True
    elif normalized_action in {"unload", "disable"}:
        enabled = False
    else:
        raise ValueError(f"Unsupported plugin management action '{action}'.")

    record = PluginRecord.objects.get(plugin_id=plugin_id)
    try:
        record.set_enabled(enabled)
    except Exception as error:
        logger.warning('Plugin availability change failed', extra={
            'event': 'plugin_state_change_failed', 'operation': 'enable' if enabled else 'disable',
            'outcome': 'failed', 'error_type': type(error).__name__})
        raise
    logger.info('Plugin availability changed', extra={'event': 'plugin_state_changed',
                'operation': 'enable' if enabled else 'disable', 'outcome': 'completed'})
    return record.enabled


def create_uploaded_plugin(form: PluginUploadForm, user) -> PluginRecord:
    if not form.is_valid():
        raise ValueError("A valid plugin upload form is required.")
    artifact = form.cleaned_data["artifact"]
    storage_name = f"{artifact.content_hash}.zip"
    manifest = artifact.package_manifest
    record = PluginRecord(
        plugin_id=manifest["id"],
        name=manifest["name"],
        version=manifest.get("version","1.0.0"),
        api_version=manifest.get("api_version","1.0"),
        package_manifest=manifest,
        compatibility=manifest.get("compatibility", "both"),
        signing_key=artifact.signing_key,
        enabled=False,
        source=PluginRecord.Source.UPLOAD,
        artifact_type=PluginRecord.ArtifactType.ZIP,
        content_hash=artifact.content_hash,
        uploaded_by=user if isinstance(user, get_user_model()) else None,
        uploaded_at=timezone.now(),
    )
    record.artifact.save(storage_name, artifact, save=False)
    try:
        with transaction.atomic():
            record.save()
    except Exception:
        logger.exception('Signed plugin could not be saved', extra={'event': 'plugin_upload_failed'})
        # Storage is outside the DB transaction; remove only this newly stored file.
        record.artifact.delete(save=False)
        raise
    logger.info('Signed plugin uploaded', extra={'event': 'plugin_uploaded', 'outcome': 'completed'})
    return record


def reload_plugins() -> list[DiscoveryResult]:
    """Discover and sync all plugins, invalidating import caches first."""
    logger.info("Reloading plugins")
    results = registry.reload()
    loaded = sum(1 for result in results if result.ok)
    failed = len(results) - loaded
    logger.info("Plugin discovery completed", extra={'event': 'plugins_reloaded',
                'loaded_count': loaded, 'failed_count': failed})
    return results
