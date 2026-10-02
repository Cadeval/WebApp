from django.conf import settings
from django.db import models
from .storage import PrivatePluginStorage


class PluginActivationError(ValueError):
    """Raised when an unsafe plugin activation is requested."""


class PluginRecord(models.Model):
    """Persisted enabled/error state for a plugin discovered via entry points.

    A record is created/updated every time discovery runs (app startup, the
    ``plugins`` management command, or a test). The ``enabled`` flag is the
    only field an administrator is expected to change at runtime; toggling it
    does not require a restart, it simply changes what
    ``PluginRegistry.get_active`` returns.
    """

    class Source(models.TextChoices):
        PACKAGE = "package", "Installed package"
        UPLOAD = "upload", "Uploaded file"

    class ArtifactType(models.TextChoices):
        NONE = "", "None"
        JAVASCRIPT = "js", "JavaScript"
        WEBASSEMBLY = "wasm", "WebAssembly"
        ZIP = "zip", "Browser package ZIP"

    plugin_id = models.CharField(max_length=255, unique=True, db_index=True)
    name = models.CharField(max_length=255, blank=True, default="")
    version = models.CharField(max_length=50, blank=True, default="")
    api_version = models.CharField(max_length=20, blank=True, default="")
    priority = models.IntegerField(default=100)
    enabled = models.BooleanField(default=True)
    error = models.TextField(blank=True, default="")
    source = models.CharField(
        max_length=16, choices=Source.choices, default=Source.PACKAGE
    )
    artifact_type = models.CharField(
        max_length=8,
        choices=ArtifactType.choices,
        default=ArtifactType.NONE,
        blank=True,
    )
    artifact = models.FileField(upload_to="plugins/", blank=True, storage=PrivatePluginStorage())
    signing_key = models.ForeignKey("PluginSigningKey",null=True,blank=True,on_delete=models.PROTECT,related_name="plugins")
    package_manifest = models.JSONField(default=dict, blank=True)
    content_hash = models.CharField(max_length=64, blank=True, default="")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_plugins",
    )
    uploaded_at = models.DateTimeField(null=True, blank=True)
    discovered_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "plugin_manager_plugin_record"
        ordering = ["priority", "plugin_id"]

    def __str__(self) -> str:
        return f"{self.name or self.plugin_id} ({self.plugin_id})"

    @property
    def has_error(self) -> bool:
        return bool(self.error)

    def set_enabled(self, enabled: bool) -> None:
        if enabled and self.artifact_type == self.ArtifactType.ZIP and (not self.signing_key or self.signing_key.revoked_at or self.signing_key.owner_id is None):
            raise PluginActivationError("A package requires an active registered signing key before it can be enabled.")
        if enabled and self.has_error:
            raise PluginActivationError(
                f"Plugin '{self.plugin_id}' cannot be enabled until its discovery error is resolved."
            )
        if enabled:
            # Conditional update closes the race with discovery setting an error.
            updated = type(self).objects.filter(pk=self.pk, error="").update(enabled=True)
            if not updated:
                raise PluginActivationError(f"Plugin '{self.plugin_id}' cannot be enabled until its discovery error is resolved.")
        else:
            type(self).objects.filter(pk=self.pk).update(enabled=False)
        self.enabled = enabled


class PluginSigningKey(models.Model):
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL,related_name="plugin_signing_keys")
    label=models.CharField(max_length=120)
    fingerprint=models.CharField(max_length=64,unique=True)
    public_key=models.CharField(max_length=44)
    created_at=models.DateTimeField(auto_now_add=True)
    revoked_at=models.DateTimeField(null=True,blank=True)

    class Meta:
        ordering=["-created_at"]

    def __str__(self): return f"{self.label} ({self.fingerprint[:16]})"
