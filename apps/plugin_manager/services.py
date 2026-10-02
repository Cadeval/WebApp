import functools
import logging
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from apps.plugin_manager.forms import PluginUploadForm
from apps.plugin_manager.models import PluginRecord
from apps.plugin_manager.registry import DiscoveryResult, registry

logger = logging.getLogger("plugin_manager")


def _is_staff(user) -> bool:
    return bool(user and user.is_active and user.is_staff)


def staff_required(view_func):
    """Require staff access: redirect anonymous users to login, but return a
    403 (rather than redirecting) for authenticated users who are not staff.
    """

    @login_required(login_url="/accounts/login/")
    @functools.wraps(view_func)
    def _wrapped(request: HttpRequest, *args, **kwargs):
        if not _is_staff(request.user):
            raise PermissionDenied("Only staff users may manage plugins.")
        return view_func(request, *args, **kwargs)

    return _wrapped


def manage_plugin(plugin_id: str, action: str) -> bool:
    """Load or unload a plugin through one audited lifecycle boundary."""
    normalized_action = action.lower()
    if normalized_action in {"load", "enable"}:
        enabled = True
        verb = "Loading"
    elif normalized_action in {"unload", "disable"}:
        enabled = False
        verb = "Unloading"
    else:
        raise ValueError(f"Unsupported plugin management action '{action}'.")

    record = PluginRecord.objects.get(plugin_id=plugin_id)
    logger.info("%s plugin '%s'", verb, plugin_id)
    record.set_enabled(enabled)
    return record.enabled


def create_uploaded_plugin(form: PluginUploadForm, user) -> PluginRecord:
    if not form.is_valid():
        raise ValueError("A valid plugin upload form is required.")
    artifact = form.cleaned_data["artifact"]
    suffix = Path(artifact.name).suffix.lower()
    storage_name = f"{artifact.content_hash}{suffix}"
    record = PluginRecord(
        plugin_id=form.cleaned_data["plugin_id"],
        name=form.cleaned_data["name"],
        version="1.0.0",
        api_version="1.0",
        enabled=False,
        source=PluginRecord.Source.UPLOAD,
        artifact_type=artifact.plugin_type,
        content_hash=artifact.content_hash,
        uploaded_by=user if isinstance(user, get_user_model()) else None,
        uploaded_at=timezone.now(),
    )
    record.artifact.save(storage_name, artifact, save=False)
    try:
        with transaction.atomic():
            record.save()
    except Exception:
        # Storage is outside the DB transaction; remove only this newly stored file.
        record.artifact.delete(save=False)
        raise
    logger.info(
        "Uploaded sandboxed %s plugin '%s'", record.artifact_type, record.plugin_id
    )
    return record


def reload_plugins() -> list[DiscoveryResult]:
    """Discover and sync all plugins, invalidating import caches first."""
    logger.info("Reloading plugins")
    results = registry.reload()
    loaded = sum(1 for result in results if result.ok)
    failed = len(results) - loaded
    logger.info("Reloaded plugins summary: %d loaded, %d failed", loaded, failed)
    return results
