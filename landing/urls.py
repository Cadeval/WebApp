from django.urls import path, include

from landing import views
from debug_toolbar.toolbar import debug_toolbar_urls


urlpatterns = [
    path("", views.index, name="index"),
    path("about/", views.index, name="index"),
    path("accounts/user/", views.user, name="user"),
    path("accounts/", include("django.contrib.auth.urls")),
    ]  + debug_toolbar_urls()
