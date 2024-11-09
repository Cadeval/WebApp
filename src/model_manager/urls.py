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
    # Landing page of the model manager
    path("model_manager/", views.model_manager, name="model_manager"),
    # Model Manager sub pages
    path("calculate_model/", views.calculate_model, name="calculate_model"),
    path("change_group/", views.change_group, name="update_group"),
    path("delete_file/", views.delete_file, name="delete_file"),
    path("delete_model/", views.delete_model, name="delete_model"),
    # Currently broken TT
    path("file_upload/", views.model_manager, name="file_upload"),
    #
    path("model_comparison/", views.model_comparison, name="model_comparison"),
    path("object_view/", views.object_view, name="object_view"),
]
