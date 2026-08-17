from django.urls import include, path

urlpatterns = [
    path("", include("apps.mycelium.urls")),
]
