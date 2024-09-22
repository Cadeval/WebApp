from debug_toolbar.toolbar import debug_toolbar_urls
from django.urls import path, include

from model_manager import views

urlpatterns = [
    path("", views.index, name="index"),
    path("about/", views.index, name="index"),
    path("accounts/", include("django.contrib.auth.urls")),
    path("accounts/user/", views.user, name="user"),
    path("model_manager/", views.model_manager, name="model_manager"),
    path("object_view/", views.object_view, name="object_view"),
]  + debug_toolbar_urls()
