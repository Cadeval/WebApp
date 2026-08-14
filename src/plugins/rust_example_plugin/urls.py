from django.urls import path

from plugins.rust_example_plugin import views

app_name = "rust_example_plugin"

urlpatterns = [
    path("plugins/rust-snake/", views.snake_game, name="snake_game"),
]