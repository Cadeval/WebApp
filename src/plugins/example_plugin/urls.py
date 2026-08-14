from django.urls import path

from plugins.example_plugin import views

app_name = "example_plugin"

urlpatterns = [
    path("plugins/ifc-editor/", views.ifc_editor, name="ifc_editor"),
]
