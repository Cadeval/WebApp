# from plugin_manager.services import create_uploaded_plugin
# from plugin_manager.forms import PluginUploadForm
# from plugin_manager.services import staff_required
import msgspec
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.views.decorators.vary import vary_on_headers
from django_bolt import BoltAPI, IsAuthenticated, JWTAuthentication, Response
from django.shortcuts import render
from apps.shared.page_views import render_page
from apps.shared.bolt_pages import page_endpoint
from django_bolt import AllowAny
from django_bolt.request import Request

from apps import plugin_manager
from apps.plugin_manager.forms import PluginUploadForm
from apps.plugin_manager.models import PluginActivationError, PluginRecord
from apps.plugin_manager.services import (
    create_uploaded_plugin,
    manage_plugin,
    reload_plugins,
)
from apps.shared.services import staff_required

# from apps.plugin_manager.models import PluginActivationError, PluginRecord
# from apps.plugin_manager.services import manage_plugin, reload_plugins


api = BoltAPI(namespace="plugin_manager",trailing_slash="keep",django_middleware=True)


def _toggle(request: HttpRequest, plugin_id: str, enabled: bool) -> HttpResponse:
    record = get_object_or_404(PluginRecord, plugin_id=plugin_id)
    try:
        manage_plugin(plugin_id, "load" if enabled else "unload")
    except PluginActivationError:
        record.refresh_from_db()
        response = render(request, "plugin_manager/_plugin_row.jinja2", {"record": record}, status=409)
        response["HX-Push-Url"] = "false"
        return response
    record.refresh_from_db()
    response = render(request, "plugin_manager/_plugin_toggle.jinja2", {"record": record})
    response["HX-Push-Url"] = "false"
    return response


@staff_required
def plugin_enable(request: HttpRequest, plugin_id: str) -> HttpResponse:
    return _toggle(request, plugin_id, True)


@staff_required
def plugin_disable(request: HttpRequest, plugin_id: str) -> HttpResponse:
    return _toggle(request, plugin_id, False)


@staff_required
def plugin_upload(request: HttpRequest) -> HttpResponse:
    form = PluginUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        response = render_page(
            request,
            "plugin_manager/plugins.jinja2",
            {"records": PluginRecord.objects.all(), "upload_form": form},
            status=400,
        )
        response["HX-Retarget"] = "#content-container"
        response["HX-Reswap"] = "outerHTML"
        return response
    try:
        record = create_uploaded_plugin(form, request.user)
    except IntegrityError:
        return HttpResponse("A plugin with this id already exists. Refresh the manager and choose another id.", status=409, content_type="text/plain")
    return render(
        request,
        "plugin_manager/_plugin_row.jinja2",
        {"record": record},
        status=201,
    )


@login_required(login_url="/mycelium/login")
def plugin_artifact(request: HttpRequest, plugin_id: str) -> FileResponse:
    record = get_object_or_404(
        PluginRecord,
        plugin_id=plugin_id,
        source=PluginRecord.Source.UPLOAD,
        enabled=True,
        error="",
        artifact_type__in=[PluginRecord.ArtifactType.JAVASCRIPT, PluginRecord.ArtifactType.WEBASSEMBLY],
    )
    if not record.artifact:
        raise Http404("Plugin artifact is unavailable.")
    content_type = (
        "application/wasm"
        if record.artifact_type == PluginRecord.ArtifactType.WEBASSEMBLY
        else "text/javascript"
    )
    try:
        stream = record.artifact.open("rb")
    except (OSError, ValueError) as error:
        raise Http404("Plugin artifact is unavailable.") from error
    response = FileResponse(stream, content_type=content_type)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cross-Origin-Resource-Policy"] = "same-origin"
    response["Content-Security-Policy"] = (
        "default-src 'none'; connect-src 'none'; worker-src 'none'; "
        "object-src 'none'; base-uri 'none'"
    )
    response["Cache-Control"] = "private, no-store"
    return response


@staff_required
@vary_on_headers("HX-Request")
def plugin_reload(request: HttpRequest) -> HttpResponse:
    results = reload_plugins()
    if not request.headers.get("HX-Request"):
        return redirect("/plugins/manage/")
    loaded = sum(1 for result in results if result.ok)
    failed = len(results) - loaded
    return render_page(
        request,
        "plugin_manager/plugins.jinja2",
        {
            "records": PluginRecord.objects.all(),
            "upload_form": PluginUploadForm(),
            "reload_summary": f"Discovery refreshed: {loaded} loaded, {failed} failed. Restart the server after changing installed Python code.",
        },
    )


@api.get('/plugins/manage/',name='plugin_list',guards=[AllowAny()])
@page_endpoint
@staff_required
def manager_page(request: Request):
    return render_page(request,'plugin_manager/plugins.jinja2',{'records':PluginRecord.objects.all(),'upload_form':PluginUploadForm()})

@api.post('/plugins/{plugin_id}/enable/',name='plugin_enable',guards=[AllowAny()])
@page_endpoint
def manager_enable(request: Request):
    return plugin_enable(request,request.params['plugin_id'])

@api.post('/plugins/{plugin_id}/disable/',name='plugin_disable',guards=[AllowAny()])
@page_endpoint
def manager_disable(request: Request):
    return plugin_disable(request,request.params['plugin_id'])

@api.post('/plugins/upload/',name='plugin_upload',guards=[AllowAny()])
@page_endpoint
def manager_upload(request: Request):
    return plugin_upload(request)

@api.post('/plugins/reload/',name='plugin_reload',guards=[AllowAny()])
@page_endpoint
def manager_reload(request: Request):
    return plugin_reload(request)

@api.get('/plugins/{plugin_id}/artifact/',name='plugin_artifact',guards=[AllowAny()])
@page_endpoint
def manager_artifact(request: Request):
    return plugin_artifact(request,request.params['plugin_id'])
