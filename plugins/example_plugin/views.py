from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.views.decorators.http import require_GET
from django.views.decorators.vary import vary_on_headers

from plugins.example_plugin import EXAMPLE_PLUGIN_ID
from plugin_manager.models import PluginRecord


@login_required(login_url="/accounts/login/")
@require_GET
@vary_on_headers("HX-Request")
def ifc_editor(request: HttpRequest) -> HttpResponse:
    if not request.headers.get("HX-Request"):
        return redirect("/")

    try:
        record = PluginRecord.objects.get(plugin_id=EXAMPLE_PLUGIN_ID)
    except PluginRecord.DoesNotExist:
        raise Http404("The IFC Editor plugin is not available.")

    if not record.enabled or record.has_error:
        raise Http404("The IFC Editor plugin is not available.")

    return TemplateResponse(request, "example_plugin/ifc_editor.jinja2")
