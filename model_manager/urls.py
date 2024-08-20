from django.urls import path

from model_manager import views

urlpatterns = [
    path("model_manager/", views.model_manager, name="model_manager"),
    path("object_view/", views.object_view, name="object_view"),
    path("model_comparison/", views.model_comparison, name="model_comparison"),
]
