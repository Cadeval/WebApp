# from django.urls import path
# from django_bolt import Router
#
# from plugin_manager import views
# from plugin_manager.views import PluginRecordViewSet
#
# router = Router()
# router.
# router.register(r"plugins", PluginRecordViewSet, basename="plugin-record")
#
# from django.urls import path
# from apps.plugin_manager import api

app_name = "plugin_manager"
#
urlpatterns = [
    #     # Page
    #     path("plugins/", views.plugin_list, name="plugin_list"),
    #     path("plugins/list/", views.plugin_reload, name="plugin_reload"),
    #     path("plugins/reload/", views.plugin_reload, name="plugin_reload"),
    #     path("plugins/upload/", views.plugin_upload, name="plugin_upload"),
    #     path(
    #         "plugins/<str:plugin_id>/artifact/",
    #         views.plugin_artifact,
    #         name="plugin_artifact",
    #     ),
    #     path("plugins/<str:plugin_id>/enable/", views.plugin_enable, name="plugin_enable"),
    #     path(
    #         "plugins/<str:plugin_id>/disable/", views.plugin_disable, name="plugin_disable"
    #     ),
    #     # REST Endpoints
    #     # path("api/", include(router.urls)),
]
