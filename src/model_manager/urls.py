# from debug_toolbar.toolbar import debug_toolbar_urls
from django.urls import path, include

from model_manager import views

# FIXME other urls not being shown if this one does not start with a /
urlpatterns = [
    path("", views.index, name="index"),
    path("about/", views.index, name="about"),
    # User specific pages
    path("accounts/", include("django.contrib.auth.urls")),
    path("accounts/user/", views.user, name="user"),
    path("config_editor/", views.config_editor, name="config_editor"),
    path("create_group/", views.create_group, name="create_group"),

    # Landing page of the model manager
    path("model_manager/", views.model_manager, name="model_manager"),
    # Model Manager sub-pages
    path("calculate_model/", views.calculate_model, name="calculate_model"),
    path("change_group/", views.change_group, name="update_group"),
    path("delete_config_file/", views.delete_config_file, name="delete_config_file"),
    path("delete_model_file/", views.delete_model_file, name="delete_model_file"),
    # path("delete_config/", views.delete_model, name="delete_model"),
    path("delete_model/", views.delete_model, name="delete_model"),
    path("download_csv/", views.download_csv, name="download_csv"),

    # f
    path("file_upload/", views.model_manager, name="file_upload"),
    #
    path("save_config/", views.save_config, name="save_config"),
    path("model_comparison/", views.model_comparison, name="model_comparison"),
    path("object_view/", views.object_view, name="object_view"),
    path("upload_config/", views.upload_config, name="upload_config"),
    path("update_config/", views.update_config, name="update_config"),
    path("upload_model/", views.upload_model, name="upload_model"),

]
