# from django.contrib.auth.models import Permission
# from .models import CadevilGroup


# class CadevilGroupPermissionMixin:
#     def has_perm(self, perm, obj=None):
#         if self.is_active and self.is_superuser:
#             return True
#
#         # Get user permissions
#         user_perms = self.user_permissions.all()
#
#         # Get permissions from CustomGroup
#         group_perms = Permission.objects.filter(customgroup__user=self)
#
#         # Check if the user has the permission
#         return (
#             user_perms.filter(codename=perm).exists()
#             or group_perms.filter(codename=perm).exists()
#         )
#
#     def get_group_permissions(self, obj=None):
#         return Permission.objects.filter(customgroup__user=self)
