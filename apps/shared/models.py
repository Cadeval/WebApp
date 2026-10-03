# from .models import ConfigUpload, CadevilUser  # Adjust import paths as needed
# user_model = settings.AUTH_USER_MODEL
# group_model = settings.AUTH_GROUP_MODEL
# group_model_name = group_model.split(".")[-1]
# permissions_related_name = "custom_group_set" if group_model_name == "Group" else None
import uuid

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.models import AbstractUser, Group, GroupManager, Permission
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models.query import QuerySet
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _


# This class has been copied from django.contrib.auth.models.Group
# The only additional thing set is abstract=True in meta
# This class should not be updated unless replacing it with a new version from django.contrib.auth.models.Group
class CadevilGroup(models.Model):
    id = models.UUIDField(
        primary_key=True, db_index=True, default=uuid.uuid4, editable=False
    )

    name = models.CharField(_("name"), max_length=150, unique=True)

    permissions = models.ManyToManyField(
        Permission,
        verbose_name=_("permissions"),
        blank=True,
        related_name="library_cadevil_group_permissions",
    )

    # objects = GroupManager()

    class Meta:
        verbose_name = _("group")
        verbose_name_plural = _("groups")
        abstract = True
        db_table = "library_cadevil_group"

    def __str__(self) -> str:
        return self.name

    def natural_key(self) -> tuple[str]:
        return (self.name,)


class CadevilUser(AbstractUser):
    # id = models.UUIDField(
    #     primary_key=True, db_index=True, default=uuid.uuid4, editable=False
    # )

    is_staff: models.BooleanField = models.BooleanField(
        _("is staff"),
        default=False,
        help_text=_("Designates whether the user can use moderation tools."),
    )

    is_superuser = models.BooleanField(
        _("is superuser"),
        default=False,
        help_text=_("Designates whether the user can log into this admin site."),
    )

    # View-hidden boolean field
    view_hidden = models.BooleanField(
        _("view hidden"),
        default=False,
        help_text=_("Designates whether this user can view hidden content."),
    )

    active_calculations = models.IntegerField(
        _("active calculations"),
        name="active_calculations",
        default=0,
        help_text=_("Number of currently active calculations."),
    )

    max_calculations = models.IntegerField(
        _("maximum concurrent active calculations"),
        name="max_calculations",
        default=1,
        help_text=_("Number of currently active calculations."),
    )

    uploads = models.ManyToManyField(
        "FileUpload", blank=True, related_name="file_upload"
    )

    # Theme string
    THEME_CHOICES = {
        ("light", "Light"),
        ("dark", "Dark"),
        ("auto", "Auto"),
    }
    theme = models.CharField(
        _("theme"),
        max_length=10,
        choices=THEME_CHOICES,
        default="auto",
        help_text=_("Preferred theme for the user interface."),
    )

    # active_group = models.ForeignKey(
    #     Group,
    #     verbose_name="group",
    #     blank=True,
    #     help_text="The groups this user is currently operating in.",
    #     related_name="cadeviluser_active_group",
    #     related_query_name="user",
    #     default=Group,
    #     on_delete=models.CASCADE,
    # )
    # groups = models.ManyToManyField(
    #     CadevilGroup,
    #     verbose_name="groups",
    #     blank=True,
    #     help_text="The groups this user belongs to. A user will get all permissions granted to each of their groups.",
    #     related_name="cadeviluser_set",
    #     related_query_name="user",
    # )

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        db_table = "library_cadeviluser"
        permissions = [("view_application_logs", "Can view live application logs")]

    def __str__(self) -> str:
        return self.username

    def get_custom_groups(self) -> QuerySet[Group, Group]:
        return Group.objects.filter(user=self)

    def add_to_custom_group(self, group_name) -> None:
        group, created = Group.objects.get_or_create(name=group_name)
        self.groups.add(group)

    def remove_from_custom_group(self, group_name) -> None:
        try:
            group = Group.objects.get(name=group_name)
            self.groups.remove(group)
        except Group.DoesNotExist:
            pass


@receiver(post_save, sender=CadevilUser)
def create_user_group(sender, instance, created, **kwargs) -> None:
    if created:
        group_name = f"user_{instance.username}"
        group, _ = Group.objects.get_or_create(name=group_name)
        instance.groups.add(group)


def user_directory_path(instance, filename: str) -> str:
    # file will be uploaded to MEDIA_ROOT/user_<id>/<filename>
    return f"user_{instance.user.id}/{filename}"


# Default maximum upload sizes (in bytes). These can be overridden per
# deployment via the corresponding Django settings, but a sensible
# hardcoded default is used when the setting is not configured, so
# uploads are never left completely unbounded.
DEFAULT_MAX_CONFIG_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB - CSV/XLSX config files
DEFAULT_MAX_MODEL_UPLOAD_SIZE = 500 * 1024 * 1024  # 500 MB - IFC building models


def validate_config_upload_size(value) -> None:
    """Reject config uploads (CSV/XLSX) larger than the configured maximum."""
    max_size = getattr(
        settings, "MODEL_MANAGER_MAX_CONFIG_UPLOAD_SIZE", DEFAULT_MAX_CONFIG_UPLOAD_SIZE
    )
    if value.size > max_size:
        raise ValidationError(
            f"File exceeds the maximum allowed size of {max_size} bytes."
        )


def validate_model_upload_size(value) -> None:
    """Reject IFC model uploads larger than the configured maximum."""
    max_size = getattr(
        settings, "MODEL_MANAGER_MAX_MODEL_UPLOAD_SIZE", DEFAULT_MAX_MODEL_UPLOAD_SIZE
    )
    if value.size > max_size:
        raise ValidationError(
            f"File exceeds the maximum allowed size of {max_size} bytes."
        )


class ConfigUpload(models.Model):
    id = models.UUIDField(
        primary_key=True, db_index=True, default=uuid.uuid4, editable=False
    )
    user = models.ForeignKey(CadevilUser, on_delete=models.CASCADE)

    document = models.FileField(
        upload_to=user_directory_path,
        validators=[
            FileExtensionValidator(allowed_extensions=("csv", "xlsx")),
            validate_config_upload_size,
        ],
    )
    description = models.CharField(db_index=True, max_length=255, blank=True)
    uploaded_at = models.DateTimeField(db_index=True, auto_now_add=True)

    class Meta:
        db_table = "archicad_eval_config_uploads"

    def __str__(self) -> str:
        return f"{self.description} - {self.document.name}"


class EpwUpload(models.Model):
    id = models.UUIDField(
        primary_key=True, db_index=True, default=uuid.uuid4, editable=False
    )
    user = models.ForeignKey(
        CadevilUser, on_delete=models.CASCADE, related_name="epw_uploads"
    )
    document = models.FileField(
        upload_to=user_directory_path,
        validators=[FileExtensionValidator(allowed_extensions=("epw",))],
    )
    description = models.CharField(db_index=True, max_length=255, blank=True)
    uploaded_at = models.DateTimeField(db_index=True, auto_now_add=True)

    class Meta:
        db_table = "archicad_eval_epw_uploads"
        ordering = ("description", "uploaded_at", "id")

    def __str__(self) -> str:
        return str(self.description or self.document.name)


class CalculationConfig(models.Model):
    id = models.UUIDField(
        primary_key=True, db_index=True, default=uuid.uuid4, editable=False
    )
    user = models.OneToOneField("CadevilUser", on_delete=models.CASCADE)
    config = models.JSONField()
    upload = models.ForeignKey(
        "ConfigUpload",
        on_delete=models.CASCADE,
        related_name="archicad_eval_config_file",
        default="",
    )

    class Meta:
        db_table = "archicad_eval_calculation_configs"

    def __str__(self) -> str:
        # Synchronous usage: returns the description of the 'upload'
        return f"{self.upload.description}"

    async def async_get_description(self):
        """
        Fetches the upload.description in an async context.

        This uses sync_to_async because Django's ORM is synchronous.
        """
        return await sync_to_async(lambda: self.upload.description)()


class FileUpload(models.Model):
    id = models.UUIDField(
        primary_key=True, db_index=True, default=uuid.uuid4, editable=False
    )

    user = models.ForeignKey(to=CadevilUser, on_delete=models.CASCADE)

    document = models.FileField(
        upload_to=user_directory_path,
        validators=[
            FileExtensionValidator(allowed_extensions=("ifc",)),
            validate_model_upload_size,
        ],
    )

    description = models.CharField(db_index=True, max_length=255, blank=True)
    uploaded_at = models.DateTimeField(db_index=True, auto_now_add=True)

    class Meta:
        db_table = "archicad_eval_uploads"

    def __str__(self) -> str:
        return f"{self.description} - {self.document.name}"



def cityjson_source_path(instance, filename):
    from pathlib import Path
    from django.utils.text import get_valid_filename
    return f"cityjson/{instance.upload.user_id}/{instance.upload_id}/{get_valid_filename(Path(filename).name)}"


class ModelConversion(models.Model):
    """Private original CityJSON and immutable conversion provenance."""
    upload = models.OneToOneField(FileUpload, on_delete=models.CASCADE, related_name="conversion")
    source = models.FileField(upload_to=cityjson_source_path)
    source_sha256 = models.CharField(max_length=64)
    output_sha256 = models.CharField(max_length=64)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class BuildingLocation(models.Model):
    """Owner-supplied or conversion-derived position for an uploaded building."""
    upload = models.ForeignKey(FileUpload, on_delete=models.CASCADE, related_name="building_locations")
    guid = models.CharField(max_length=22)
    latitude = models.FloatField(validators=[MinValueValidator(-90), MaxValueValidator(90)])
    longitude = models.FloatField(validators=[MinValueValidator(-180), MaxValueValidator(180)])
    source = models.CharField(max_length=24, choices=[("manual", "Owner-set location"), ("cityjson_geometry", "CityJSON geometry")], default="manual")
    source_sha256 = models.CharField(max_length=64, blank=True)
    note = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["upload", "guid"], name="unique_upload_building_location")]


class CadevilDocument(models.Model):
    id = models.UUIDField(
        primary_key=True, db_index=True, default=uuid.uuid4, editable=False
    )

    user = models.ForeignKey(CadevilUser, on_delete=models.CASCADE)
    group = models.ForeignKey(Group, on_delete=models.CASCADE)

    upload = models.ForeignKey(
        FileUpload,
        on_delete=models.CASCADE,
        related_name="user_file_upload",
        default="",
    )

    is_active = models.BooleanField(default=True)
    description = models.CharField(db_index=True, max_length=255, blank=True)
    # TODO render image
    # TODO implement using dataclasses
    # preview_image = models.ImageField()
    type = "Wohnbau"
    structure = "Rohbau"

    class Meta:
        db_table = "archicad_eval_document"


# The spacial indicators can be split into these:
# BGF (Brutto-Grundfläche / Gross Floor Area)
# └── KF (Konstruktionsfläche / Construction Area)
# └── NGF (Netto-Grundfläche) = NRF (Netto-Raumfläche)
#     ├── NUF (Nutzungsfläche / Usable Area)
#     ├── VF (Verkehrsfläche / Circulation Area)
#     └── TF (Technische Funktionsfläche / Technical Area)
class BuildingMetrics(models.Model):
    assessment_report = models.JSONField(default=dict, blank=True)
    ENERGY_STATUS_CHOICES = (
        ("not_run", "Not run"),
        ("running", "Running"),
        ("success", "Success"),
        ("failed", "Failed"),
    )
    id = models.UUIDField(
        primary_key=True, db_index=True, default=uuid.uuid4, editable=False
    )

    project = models.ForeignKey(
        "CadevilDocument", on_delete=models.CASCADE, related_name="building_metrics"
    )

    # Grundstück (Property)
    grundstuecksfläche = models.FloatField(default=0.0, verbose_name="GF")
    grundstuecksfläche_unit = models.CharField(max_length=10, default="m2")

    bebaute_fläche = models.FloatField(default=0.0, verbose_name="BF")
    bebaute_fläche_unit = models.CharField(max_length=10, default="m2")

    unbebaute_fläche = models.FloatField(default=0.0, verbose_name="UF")
    unbebaute_fläche_unit = models.CharField(max_length=10, default="m2")

    # Brutto Measurements
    brutto_rauminhalt = models.FloatField(default=0.0, verbose_name="BRI")
    brutto_rauminhalt_unit = models.CharField(max_length=10, default="m3")

    brutto_grundfläche = models.FloatField(default=0.0, verbose_name="BGF")
    brutto_grundfläche_unit = models.CharField(max_length=10, default="m2")

    # Component Areas
    konstruktions_grundfläche = models.FloatField(default=0.0, verbose_name="KGF")
    konstruktions_grundfläche_unit = models.CharField(max_length=10, default="m2")

    netto_raumfläche = models.FloatField(default=0.0, verbose_name="NRF")
    netto_raumfläche_unit = models.CharField(max_length=10, default="m2")

    # Ratios
    bgf_bf_ratio = models.FloatField(default=0.0, verbose_name="BGF/BF")
    bri_bgf_ratio = models.FloatField(default=0.0, verbose_name="BRI/BGF")

    # Additional Metrics
    fassadenflaeche = models.FloatField(default=0.0, verbose_name="Facade Area")
    fassadenflaeche_unit = models.CharField(max_length=10, default="m2")

    fassaden_oeffnungsflaeche = models.FloatField(
        default=0.0, verbose_name="Facade Opening Area"
    )
    fassaden_oeffnungsflaeche_unit = models.CharField(max_length=10, default="m2")

    fassaden_opake_flaeche = models.FloatField(
        default=0.0, verbose_name="Opaque Facade Area"
    )
    fassaden_opake_flaeche_unit = models.CharField(max_length=10, default="m2")

    fenster_wand_verhaeltnis = models.FloatField(
        default=0.0,
        validators=[MinValueValidator(0.0)],
        verbose_name="Window-to-Wall Ratio",
    )

    stockwerke = models.FloatField(default=0.0, verbose_name="Stockwerke")
    energie_bewertung = models.CharField(
        max_length=50, default="Unknown", verbose_name="Energy Rating"
    )

    energy_status = models.CharField(
        max_length=20, choices=ENERGY_STATUS_CHOICES, default="not_run"
    )
    annual_site_energy_kwh = models.FloatField(
        blank=True, null=True, validators=[MinValueValidator(0.0)]
    )
    annual_electricity_kwh = models.FloatField(
        blank=True, null=True, validators=[MinValueValidator(0.0)]
    )
    annual_natural_gas_kwh = models.FloatField(
        blank=True, null=True, validators=[MinValueValidator(0.0)]
    )
    energy_use_intensity_kwh_m2_year = models.FloatField(
        blank=True, null=True, validators=[MinValueValidator(0.0)]
    )
    energy_error = models.TextField(blank=True, default="")
    energy_assumptions = models.TextField(blank=True, default="")
    energy_weather_file = models.CharField(max_length=255, blank=True, default="")
    energy_weather_upload = models.ForeignKey(
        EpwUpload,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="building_metrics",
    )
    openstudio_version = models.CharField(max_length=50, blank=True, default="")
    energy_simulated_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        verbose_name = "Gebäude Kenndaten"
        verbose_name_plural = "Gebäude Kenndaten"
        db_table = "building_metrics"

    def __str__(self) -> str:
        return f" Building Metrics of (ID: {self.id})"


class MaterialProperties(models.Model):
    id = models.UUIDField(
        primary_key=True, db_index=True, default=uuid.uuid4, editable=False
    )
    project = models.ForeignKey(
        CadevilDocument, on_delete=models.CASCADE, related_name="material_properties"
    )

    name = models.CharField(max_length=100)

    global_brutto_price = models.FloatField(
        default=None, null=True, blank=True, validators=[MinValueValidator(0.0)]
    )
    local_brutto_price = models.FloatField(
        default=None, null=True, blank=True, validators=[MinValueValidator(0.0)]
    )
    local_netto_price = models.FloatField(
        default=None, null=True, blank=True, validators=[MinValueValidator(0.0)]
    )
    volume = models.FloatField(default=None, null=True, blank=True, validators=[MinValueValidator(0.0)])
    area = models.FloatField(default=None, null=True, blank=True, validators=[MinValueValidator(0.0)])
    length = models.FloatField(default=None, null=True, blank=True, validators=[MinValueValidator(0.0)])
    mass = models.FloatField(default=None, null=True, blank=True, validators=[MinValueValidator(0.0)])

    penrt_ml_a1_a3 = models.FloatField(default=None, null=True, blank=True, validators=[MinValueValidator(0.0)])
    gwp_ml_a1_a3 = models.FloatField(default=None, null=True, blank=True)
    ap_ml_a1_a3 = models.FloatField(default=None, null=True, blank=True, validators=[MinValueValidator(0.0)])

    penrt_ml_a1_a3_b4 = models.FloatField(
        default=None, null=True, blank=True, validators=[MinValueValidator(0.0)]
    )
    gwp_ml_a1_a3_b4 = models.FloatField(default=None, null=True, blank=True
    )
    ap_ml_a1_a3_b4 = models.FloatField(default=None, null=True, blank=True, validators=[MinValueValidator(0.0)])

    penrt_ml_lz = models.FloatField(default=None, null=True, blank=True, validators=[MinValueValidator(0.0)])
    gwp_ml_lz = models.FloatField(default=None, null=True, blank=True)
    ap_ml_lz = models.FloatField(default=None, null=True, blank=True, validators=[MinValueValidator(0.0)])

    recyclable_mass = models.FloatField(
        default=None, null=True, blank=True, validators=[MinValueValidator(0.0)]
    )
    waste_mass = models.FloatField(default=None, null=True, blank=True, validators=[MinValueValidator(0.0)])

    class Meta:
        verbose_name_plural = "Material properties"
        db_table = "material_properties"

    def __str__(self) -> str:
        return f"<Material: {self.name}> (ID: {self.id})"


admin.site.register(CadevilDocument)
admin.site.register(FileUpload)

# Disconnect the signal if it's already connected (useful in development to avoid duplicate connections)
# TODO remove this?
post_save.disconnect(create_user_group, sender=CadevilUser)
# Reconnect the signal
post_save.connect(create_user_group, sender=CadevilUser)
