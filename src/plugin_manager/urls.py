from django.urls import include, path
from rest_framework.routers import DefaultRouter

from plugin_manager import views
from plugin_manager.views import PluginRecordViewSet

router = DefaultRouter()
router.register(r"plugins", PluginRecordViewSet, basename="plugin-record")

app_name = "plugin_manager"

urlpatterns = [
    # Page
    path("plugins/", views.plugin_list, name="plugin_list"),
    path("plugins/reload/", views.plugin_reload, name="plugin_reload"),
    path("plugins/upload/", views.plugin_upload, name="plugin_upload"),
    path("plugins/<str:plugin_id>/artifact/", views.plugin_artifact, name="plugin_artifact"),
    path("plugins/<str:plugin_id>/enable/", views.plugin_enable, name="plugin_enable"),
    path("plugins/<str:plugin_id>/disable/", views.plugin_disable, name="plugin_disable"),
    # REST Endpoints
    path("api/", include(router.urls)),
]
