from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from model_manager.models import FileUpload, CadevilDocument, CadevilUser  # , CadevilGroup

admin.site.register(CadevilDocument)
admin.site.register(FileUpload)


# Custom User Admin
class CadevilUserAdmin(UserAdmin):
    model = CadevilUser
    list_display = ["username", "email", "view_hidden", "theme"]
    fieldsets = UserAdmin.fieldsets + (
        ("Custom Fields", {"fields": ("view_hidden", "theme")}),
    )


# Custom Group Admin
# class CadevilGroupAdmin(BaseGroupAdmin):
#     model = CadevilGroup
#     list_display = ("name", "description", "created_at", "updated_at")
#     list_filter = ("created_at", "updated_at")
#     search_fields = ("name", "description")
#
#
# # Unregister the original Group model
# admin.site.unregister(BaseGroup)
#
# # Register custom models
admin.site.register(CadevilUser, CadevilUserAdmin)
# admin.site.register(CadevilGroup, CadevilGroupAdmin)
