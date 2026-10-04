from django.conf import settings
from django.db import models
import uuid
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
        ZIP = "zip", "Browser package ZIP"

    class Compatibility(models.TextChoices):
        DEBUG = "debug", "Debug only"
        PRODUCTION = "production", "Production only"
        BOTH = "both", "Debug and production"

    compatibility = models.CharField(max_length=16, choices=Compatibility.choices, default=Compatibility.BOTH)

    plugin_id = models.CharField(max_length=255, unique=True, db_index=True)
    name = models.CharField(max_length=255, blank=True, default="")
    version = models.CharField(max_length=50, blank=True, default="")
    version_history = models.JSONField(default=list, db_default=[], blank=True)
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

    def observe_version(self, *, observed_at=None, basis="verified") -> bool:
        """Append a successfully observed version without inventing prior releases."""
        from django.utils import timezone

        if not self.version:
            return False
        history = list(self.version_history or [])
        identity = (self.version, self.api_version, self.source, self.content_hash)
        if any((entry.get("version"), entry.get("api_version", ""),
                entry.get("source"), entry.get("content_hash", "")) == identity
               for entry in history if isinstance(entry, dict)):
            return False
        history.append({"version": self.version, "api_version": self.api_version,
                        "source": self.source, "content_hash": self.content_hash,
                        "observed_at": (observed_at or timezone.now()).isoformat(),
                        "basis": basis})
        self.version_history = history
        return True

    @property
    def has_error(self) -> bool:
        return bool(self.error)

    @property
    def environment_compatible(self):
        from .environments import compatible
        return compatible(self.compatibility)

    @property
    def effective_enabled(self):
        return self.enabled and not self.has_error and self.environment_compatible

    def set_enabled(self, enabled: bool) -> None:
        if enabled and not self.environment_compatible:
            raise PluginActivationError(f"This plugin is {self.get_compatibility_display().lower()} and unavailable in the current environment.")
        if enabled and self.source == self.Source.UPLOAD and self.artifact_type != self.ArtifactType.ZIP:
            raise PluginActivationError("Repackage this legacy upload as a signed archive before enabling it.")
        if enabled and self.artifact_type == self.ArtifactType.ZIP:
            from .certificate_authority import trusted_key
            if not trusted_key(self.signing_key):
                raise PluginActivationError("A package requires a valid, unrevoked code-signing certificate before it can be enabled.")
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
    team=models.ForeignKey("Team",null=True,blank=True,on_delete=models.PROTECT,related_name="signing_keys")
    certificate_authority=models.ForeignKey("PluginCertificateAuthority",null=True,blank=True,on_delete=models.PROTECT,related_name="signing_keys")
    certificate=models.TextField(blank=True,default="",db_default="")
    certificate_serial=models.CharField(max_length=40,blank=True,default="",db_default="")

    class Meta:
        ordering=["-created_at"]

    def __str__(self): return f"{self.label} ({self.fingerprint[:16]})"


class Team(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    name=models.CharField(max_length=120)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL,related_name="created_plugin_teams")
    active=models.BooleanField(default=True)
    created_at=models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering=["name","id"]

    def __str__(self): return self.name


class TeamMembership(models.Model):
    class Role(models.TextChoices):
        MEMBER="member","Member"
        MANAGER="manager","Manager"

    team=models.ForeignKey(Team,on_delete=models.CASCADE,related_name="memberships")
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name="plugin_team_memberships")
    role=models.CharField(max_length=12,choices=Role.choices,default=Role.MEMBER)
    created_at=models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=["team","user"],name="unique_plugin_team_member")]


class PluginCertificateAuthority(models.Model):
    fingerprint=models.CharField(max_length=64,unique=True)
    certificate=models.TextField()
    intermediate_certificate=models.TextField()
    publisher_certificate=models.TextField(blank=True,default="")
    active=models.BooleanField(default=True)
    created_at=models.DateTimeField(auto_now_add=True)
    revoked_at=models.DateTimeField(null=True,blank=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=["active"],condition=models.Q(active=True),name="one_active_plugin_ca")]


class SigningCertificateRevocation(models.Model):
    authority=models.ForeignKey(PluginCertificateAuthority,on_delete=models.PROTECT,related_name="revocations")
    serial=models.CharField(max_length=40)
    revoked_at=models.DateTimeField(auto_now_add=True)
    reason=models.CharField(max_length=120,default="Revoked")

    class Meta:
        constraints=[models.UniqueConstraint(fields=["authority","serial"],name="unique_plugin_revoked_serial")]


class UserPluginSelection(models.Model):
    """An explicit workflow choice; site availability remains administrator-owned."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="plugin_selections")
    plugin = models.ForeignKey(PluginRecord, on_delete=models.CASCADE,
                               related_name="user_selections")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "plugin"],
                                               name="unique_user_plugin_selection")]

    def __str__(self):
        return f"{self.user_id}: {self.plugin.plugin_id}"
