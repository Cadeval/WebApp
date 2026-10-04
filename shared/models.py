"""Host user identities and personal groups, independent of optional plugins."""
import uuid

from django.contrib.auth.models import AbstractUser, Group, Permission
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

    # Theme string
    THEME_CHOICES = (
        ("dark", "Dark"),
        ("light", "Light"),
        ("auto", "Auto"),
    )
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
