from django.urls import path, include

from landing import views


urlpatterns = [
    path("", views.index, name="index"),
    path("about/", views.index, name="index"),
    path("accounts/user/", views.user, name="user"),
    path("accounts/", include("django.contrib.auth.urls")),
]
