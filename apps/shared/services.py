import functools

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.views.decorators.vary import vary_on_headers

# from plugin_manager.forms import PluginUploadForm
# from plugin_manager.models import PluginRecord

# from plugin_manager.forms import PluginUploadForm
# from plugin_manager.models import PluginRecord


def _is_staff(user) -> bool:
    return bool(user and user.is_active and user.is_staff)


def staff_required(view_func):
    """Require staff access: redirect anonymous users to login, but return a
    403 (rather than redirecting) for authenticated users who are not staff.
    """

    @login_required(login_url="/mycelium/login")
    @functools.wraps(view_func)
    def _wrapped(request: HttpRequest, *args, **kwargs):
        if not _is_staff(request.user):
            raise PermissionDenied("Only staff users may manage plugins.")
        return view_func(request, *args, **kwargs)

    return _wrapped


# @staff_required
# @vary_on_headers("HX-Request")
# def plugin_list(request: HttpRequest) -> HttpResponse:
#     if not request.headers.get("HX-Request"):
#         return redirect("/")
#
#     records = PluginRecord.objects.all()
#     return TemplateResponse(
#         request,
#         "plugin_manager/plugins.jinja2",
#         {"records": records, "upload_form": PluginUploadForm()},
#     )
