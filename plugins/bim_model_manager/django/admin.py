"""Admin registrations for BIM Workspace models."""
from django.contrib import admin

from .models import CadevilDocument, FileUpload


admin.site.register(CadevilDocument)
admin.site.register(FileUpload)
