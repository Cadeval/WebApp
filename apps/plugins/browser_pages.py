"""Native Bolt pages for the built-in browser workers."""
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django_bolt import AllowAny, BoltAPI
from apps.shared.request_logging import configure_api_logging
from apps.shared.bolt_pages import page_endpoint
from apps.shared.page_views import render_page
from apps.plugin_manager.workflows import workflow_plugin_enabled

api = BoltAPI(trailing_slash="keep", django_middleware=True)
configure_api_logging(api)


def browser_page(request, plugin_id, template):
    if not workflow_plugin_enabled(request.user, plugin_id):
        raise Http404("This plugin is not available.")
    return render_page(request, template)


@api.get("/plugins/ifc-editor/", guards=[AllowAny()])
@page_endpoint
@login_required(login_url="/mycelium/login")
def ifc_editor(request):
    return browser_page(request, "cadevil.example.editor", "example_plugin/ifc_editor.jinja2")


@api.get("/plugins/rust-snake/", guards=[AllowAny()])
@page_endpoint
@login_required(login_url="/mycelium/login")
def snake_game(request):
    return browser_page(request, "cadevil.rust-example.editor", "rust_example_plugin/snake_game.jinja2")
