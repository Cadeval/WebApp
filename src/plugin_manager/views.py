import functools

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.views.decorators.http import require_POST
from django.views.decorators.vary import vary_on_headers
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from plugin_manager.forms import PluginUploadForm
from plugin_manager.models import PluginActivationError, PluginRecord
from plugin_manager.serializers import PluginRecordSerializer
from plugin_manager.services import (
    create_uploaded_plugin,
    manage_plugin,
    reload_plugins,
)


class PluginRecordViewSet(viewsets.ReadOnlyModelViewSet):
    """List/retrieve discovered plugins, and enable/disable them.

    Restricted to admin users. Enabling/disabling a plugin changes which of
    its contributed extensions ``PluginRegistry.get_active`` returns. The
    collection reload action discovers package changes without a restart.
    """

    queryset = PluginRecord.objects.all()
    serializer_class = PluginRecordSerializer
    permission_classes = [IsAdminUser]
    lookup_field = "plugin_id"
    lookup_value_regex = "[^/]+"

    @action(detail=True, methods=["post"])
    def enable(self, request, plugin_id=None) -> Response:
        record = self.get_object()
        try:
            manage_plugin(record.plugin_id, "load")
        except PluginActivationError as error:
            return Response({"detail": str(error)}, status=409)
        record.refresh_from_db()
        return Response(self.get_serializer(record).data)

    @action(detail=True, methods=["post"])
    def disable(self, request, plugin_id=None) -> Response:
        record = self.get_object()
        manage_plugin(record.plugin_id, "unload")
        record.refresh_from_db()
        return Response(self.get_serializer(record).data)

    @action(detail=False, methods=["post"])
    def reload(self, request) -> Response:
        results = reload_plugins()
        loaded = sum(1 for result in results if result.ok)
        errors = [
            {"plugin_id": result.plugin_id, "error": result.error}
            for result in results
            if not result.ok
        ]
        return Response(
            {
                "detail": "Plugin reload completed.",
                "total": len(results),
                "loaded": loaded,
                "failed": len(errors),
                "errors": errors,
            }
        )


def _is_staff(user) -> bool:
    return bool(user and user.is_active and user.is_staff)


def staff_required(view_func):
    """Require staff access: redirect anonymous users to login, but return a
    403 (rather than redirecting) for authenticated users who are not staff.
    """

    @login_required(login_url="/accounts/login/")
    @functools.wraps(view_func)
    def _wrapped(request: HttpRequest, *args, **kwargs):
        if not _is_staff(request.user):
            raise PermissionDenied("Only staff users may manage plugins.")
        return view_func(request, *args, **kwargs)

    return _wrapped


@staff_required
@vary_on_headers("HX-Request")
def plugin_list(request: HttpRequest) -> HttpResponse:
    if not request.headers.get("HX-Request"):
        return redirect("/")

    records = PluginRecord.objects.all()
    return TemplateResponse(
        request,
        "plugin_manager/plugins.jinja2",
        {"records": records, "upload_form": PluginUploadForm()},
    )


def _toggle(request: HttpRequest, plugin_id: str, enabled: bool) -> HttpResponse:
    record = get_object_or_404(PluginRecord, plugin_id=plugin_id)
    try:
        manage_plugin(plugin_id, "load" if enabled else "unload")
    except PluginActivationError as error:
        return HttpResponse(str(error), status=409, content_type="text/plain")
    return TemplateResponse(request, "plugin_manager/_plugin_row.jinja2", {"record": record})


@staff_required
@require_POST
def plugin_enable(request: HttpRequest, plugin_id: str) -> HttpResponse:
    return _toggle(request, plugin_id, True)


@staff_required
@require_POST
def plugin_disable(request: HttpRequest, plugin_id: str) -> HttpResponse:
    return _toggle(request, plugin_id, False)


@staff_required
@require_POST
def plugin_upload(request: HttpRequest) -> HttpResponse:
    form = PluginUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        response = TemplateResponse(
            request,
            "plugin_manager/plugins.jinja2",
            {"records": PluginRecord.objects.all(), "upload_form": form},
            status=400,
        )
        response["HX-Retarget"] = "#content-container"
        response["HX-Reswap"] = "outerHTML"
        return response
    record = create_uploaded_plugin(form, request.user)
    return TemplateResponse(
        request,
        "plugin_manager/_plugin_row.jinja2",
        {"record": record},
        status=201,
    )


@login_required(login_url="/accounts/login/")
def plugin_artifact(request: HttpRequest, plugin_id: str) -> FileResponse:
    record = get_object_or_404(
        PluginRecord,
        plugin_id=plugin_id,
        source=PluginRecord.Source.UPLOAD,
        enabled=True,
    )
    if not record.artifact:
        raise Http404("Plugin artifact is unavailable.")
    content_type = (
        "application/wasm"
        if record.artifact_type == PluginRecord.ArtifactType.WEBASSEMBLY
        else "text/javascript"
    )
    response = FileResponse(record.artifact.open("rb"), content_type=content_type)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cross-Origin-Resource-Policy"] = "same-origin"
    response["Content-Security-Policy"] = (
        "default-src 'none'; connect-src 'none'; worker-src 'none'; "
        "object-src 'none'; base-uri 'none'"
    )
    response["Cache-Control"] = "private, no-store"
    return response

@staff_required
@require_POST
@vary_on_headers("HX-Request")
def plugin_reload(request: HttpRequest) -> HttpResponse:
    if not request.headers.get("HX-Request"):
        return redirect("/")
    results = reload_plugins()
    loaded = sum(1 for result in results if result.ok)
    failed = len(results) - loaded
    return TemplateResponse(
        request,
        "plugin_manager/plugins.jinja2",
        {
            "records": PluginRecord.objects.all(),
            "upload_form": PluginUploadForm(),
            "reload_summary": f"Reload complete: {loaded} loaded, {failed} failed.",
        },
    )
