from django.urls import path

from .views import MyceliumView

app_name = "mycelium"

urlpatterns = [
    path("", MyceliumView.as_view(), name="mycelium"),
]
