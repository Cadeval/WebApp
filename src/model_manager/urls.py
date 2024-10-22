# from debug_toolbar.toolbar import debug_toolbar_urls
from django.urls import path, include

from model_manager import views

# FIXME other urls not being shown if this one does not start with a /
urlpatterns = [
    path("", views.index, name="index"),
    path("about/", views.index, name="about"),
    path("accounts/", include("django.contrib.auth.urls")),
    path("accounts/user/", views.user, name="user"),
    path("file_upload/", views.model_manager, name="file_upload"),
    path("model_manager/", views.model_manager, name="model_manager"),
    path("model_comparison/", views.model_comparison, name="model_comparison"),
    path("object_view/", views.object_view, name="object_view"),
]