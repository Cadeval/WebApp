import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser, Permission, Group, GroupManager
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

# user_model = settings.AUTH_USER_MODEL
# group_model = settings.AUTH_GROUP_MODEL
# group_model_name = group_model.split(".")[-1]
# permissions_related_name = "custom_group_set" if group_model_name == "Group" else None


def user_directory_path(instance, filename):
    # file will be uploaded to MEDIA_ROOT/user_<id>/<filename>
    return f"user_{instance.user.id}/{filename}"


# This class has been copied from django.contrib.auth.models.Group
# The only additional thing set is abstract=True in meta
# This class should not be updated unless replacing it with a new version from django.contrib.auth.models.Group
# class CadevilGroup(models.Model):
#     name = models.CharField(_("name"), max_length=150, unique=True)
#     permissions = models.ManyToManyField(
#         Permission,
#         verbose_name=_("permissions"),
#         blank=True,
#         related_name=permissions_related_name,
#     )
#
#     objects = GroupManager()
#
#     class Meta:
#         verbose_name = _("group")
#         verbose_name_plural = _("groups")
#         abstract = True
#         db_table = "library_cadevigroup"
#
#     def __str__(self):
#         return self.name
#
#     def natural_key(self):
#         return (self.name,)


class CadevilUser(AbstractUser):
    id = models.UUIDField(
        primary_key=True, db_index=True, default=uuid.uuid4, editable=False
    )

    is_staff = models.BooleanField(
        _("is staff"), default=False, help_text=_("Designates whether the user can log into this admin site.")
    )

    is_superuser = models.BooleanField(
        _("is superuser"),
        default=False,
        help_text=_("Designates whether this user can view hidden content."),
    )

    # View-hidden boolean field
    view_hidden = models.BooleanField(
        _("view hidden"),
        default=False,
        help_text=_("Designates whether this user can view hidden content."),
    )

    uploads = models.ManyToManyField(
        "FileUpload", blank=True, related_name="file_upload"
    )

    # Theme string
    THEME_CHOICES = [
        ("light", "Light"),
        ("dark", "Dark"),
        ("auto", "Auto"),
    ]
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

    def __str__(self):
        return self.username

    def get_custom_groups(self):
        return Group.objects.filter(user=self)

    def add_to_custom_group(self, group_name):
        group, created = Group.objects.get_or_create(name=group_name)
        self.groups.add(group)

    def remove_from_custom_group(self, group_name):
        try:
            group = Group.objects.get(name=group_name)
            self.groups.remove(group)
        except Group.DoesNotExist:
            pass


class FileUpload(models.Model):
    id = models.UUIDField(
        primary_key=True, db_index=True, default=uuid.uuid4, editable=False
    )

    user = models.ForeignKey(CadevilUser, on_delete=models.CASCADE)

    document = models.FileField(upload_to=user_directory_path)

    description = models.CharField(db_index=True, max_length=255, blank=True)
    uploaded_at = models.DateTimeField(db_index=True, auto_now_add=True)

    class Meta:
        db_table = "archicad_eval_uploads"

    def __str__(self):
        return f"{self.description} - {self.document.name}"


class CadevilDocument(models.Model):
    id = models.UUIDField(
        primary_key=True, db_index=True, default=uuid.uuid4, editable=False
    )

    user = models.ForeignKey(CadevilUser, on_delete=models.CASCADE)
    group = models.ForeignKey(Group, on_delete=models.CASCADE)
    # TODO migrations break if not run via 'python manage.py makemigrations library'
    # idek why the heck but apparently it really wants to get told which apps to create
    # the migrations for???
    # groups = models.ManyToManyField(
    #     CadevilGroup,
    #     verbose_name="groups",
    #     blank=True,
    #     help_text="The groups this user belongs to. A user will get all permissions granted to each of their groups.",
    #     related_name="cadeviluser_set",
    #     related_query_name="user"
    # )

    is_active = models.BooleanField(default=True)
    description = models.CharField(db_index=True, max_length=255, blank=True)
    # TODO render image
    # TODO implement using dataclasses
    # preview_image = models.ImageField()
    type = "Wohnbau"
    structure = "Rohbau"
    materials = models.JSONField(db_index=True, blank=True, default=dict)
    # Initialize the properties dictionary to store various metrics
    properties = models.JSONField(
        db_index=True,
        blank=True,
        default=dict,
    )

    class Meta:
        db_table = "archicad_eval_document"


@receiver(post_save, sender=CadevilUser)
def create_user_group(sender, instance, created, **kwargs):
    if created:
        group_name = f"user_{instance.username}"
        group, _ = Group.objects.get_or_create(name=group_name)
        instance.groups.add(group)


# Disconnect the signal if it's already connected (useful in development to avoid duplicate connections)
# TODO remove this?
post_save.disconnect(create_user_group, sender=CadevilUser)
# Reconnect the signal
post_save.connect(create_user_group, sender=CadevilUser)