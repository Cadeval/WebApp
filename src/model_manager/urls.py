# from debug_toolbar.toolbar import debug_toolbar_urls
import pprint

from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from model_manager import views

from rest_framework.routers import DefaultRouter
from model_manager.views import CalculationConfigViewSet, CadevilDocumentViewSet, ConfigUploadViewSet, \
    FileUploadViewSet, BuildingMetricsViewSet, MaterialPropertiesViewSet

router = DefaultRouter()
router.register(r'building_metrics', BuildingMetricsViewSet)
router.register(r'calculation_config', CalculationConfigViewSet)
router.register(r'cadevil_document', CadevilDocumentViewSet)
router.register(r'config_file', ConfigUploadViewSet)
router.register(r'material_properties', MaterialPropertiesViewSet)
router.register(r'model_file', FileUploadViewSet)

urlpatterns = [
    # Pages
    path("", views.index, name="index"),
    path("about/", views.index, name="about"),
    # User specific pages
    path("accounts/", include("django.contrib.auth.urls")),
    path("accounts/user/", views.user, name="user"),
    path("config_editor/", views.config_editor, name="config_editor"),
    path("model_comparison/", views.model_comparison, name="model_comparison"),
    path("model_manager/", views.model_manager, name="model_manager"),
    path('api/', include(router.urls)),  # API root view is automatically created

    # REST Endpoints
    path("create_group/", views.create_group, name="create_group"),
    path("change_group/", views.change_group, name="update_group"),
    path("delete_config_file/", views.delete_config_file, name="delete_config_file"),
    path("delete_model_file/", views.delete_model_file, name="delete_model_file"),
    # path("delete_config/", views.delete_model, name="delete_model"),
    path("download_csv/", views.download_csv, name="download_csv"),
    path("file_upload/", views.model_manager, name="file_upload"),
    path("save_config/", views.save_config, name="save_config"),
    path("object_view/", views.object_view, name="object_view"),
    path("upload_config/", views.upload_config, name="upload_config"),
    path("update_config/", views.update_config, name="update_config"),
    path("upload_model/", views.upload_model, name="upload_model"),

    # OpenAPI schema endpoint
    path('schema/', SpectacularAPIView.as_view(), name='schema'),
    # Swagger UI for interactive API documentation
    path('docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),

]
