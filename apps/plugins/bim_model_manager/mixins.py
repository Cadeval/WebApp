# from django.contrib.auth.models import Permission
from django.db.models.query import _Model
from django.db.models.query import QuerySet
from django.db.models.base import Model
from model_manager.models import CadevilGroup
from django.contrib.auth.models import Permission


class CadevilGroupPermissionMixin:
    def has_perm(self, perm):
        if self.is_active and self.is_superuser:
            return True

        # Get user permissions
        user_perms = self.user_permissions.all()

        # Get permissions from CustomGroup
        group_perms = Permission.objects.filter(customgroup__user=self)

        # Check if the user has the permission
        return (
            user_perms.filter(codename=perm).exists()
            or group_perms.filter(codename=perm).exists()
        )

    def get_group_permissions(self, obj=None) -> QuerySet[Permission, Permission]:
        return Permission.objects.filter(customgroup__user=self)
