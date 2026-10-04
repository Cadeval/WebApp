"""Session-authenticated personal account settings."""
import logging
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.http import Http404, HttpResponseRedirect

from apps.shared.page_views import render_page
from .settings_forms import ProfileSettingsForm

SECTIONS = {"account", "security", "access"}
logger = logging.getLogger('cadevil.security')


def settings_context(request, *, profile_form=None, password_form=None, section=None, notice=""):
    section = section or request.GET.get("section", "account")
    if section not in SECTIONS:
        section = "account"
    password_form = password_form if password_form is not None else PasswordChangeForm(request.user)
    if not password_form.is_bound:
        password_form.fields["old_password"].widget.attrs.pop("autofocus", None)
    context = {
        "profile_form": profile_form if profile_form is not None else ProfileSettingsForm(instance=request.user),
        "password_form": password_form,
        "section": section, "notice": notice,
        "user_groups": request.user.groups.order_by("name"),
        "capacity": {"active": request.user.active_calculations, "limit": request.user.max_calculations},
        "can_view_users": bool(request.user.is_staff and request.user.has_perm("shared.view_cadeviluser")),
        "can_view_groups": bool(request.user.is_staff and request.user.has_perm("auth.view_group")),
    }
    if section == "security":
        from apps.plugin_manager.keys import signing_key_context
        context.update(signing_key_context(request))
    return context


def settings_response(request, *, status=200, **kwargs):
    response = render_page(request, "mycelium/settings.jinja2", settings_context(request, **kwargs), status=status)
    response["Cache-Control"] = "private, no-store"
    return response


def saved_response(request, section, notice):
    if request.headers.get("HX-Request") == "true":
        response = settings_response(request, section=section, notice=notice)
        response["HX-Push-Url"] = "false"
        return response
    messages.success(request, notice)
    return HttpResponseRedirect(f"/mycelium/settings?section={section}")


@login_required(login_url="/mycelium/login")
def user_settings(request):
    if request.method != "POST":
        return settings_response(request)
    action = request.POST.get("action")
    if action == "profile":
        form = ProfileSettingsForm(request.POST, instance=request.user)
        if not form.is_valid():
            return settings_response(request, profile_form=form, section="account", status=400)
        form.save()
        return saved_response(request, "account", "Account settings saved.")
    if action == "password":
        form = PasswordChangeForm(request.user, request.POST)
        if not form.is_valid():
            return settings_response(request, password_form=form, section="security", status=400)
        user = form.save()
        update_session_auth_hash(request, user)
        logger.info('Account password changed', extra={'event': 'password_changed', 'outcome': 'completed'})
        return saved_response(request, "security", "Password changed. This session remains signed in.")
    raise Http404("Unknown account settings action.")
