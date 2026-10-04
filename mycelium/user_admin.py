"""Reversible user/group administration with explicit delegated permissions."""
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404

from shared.page_views import render_page
from .settings_forms import (
    AdministrativeGroupForm, AdministrativeUserCreateForm, AdministrativeUserEditForm,
    group_assignable, user_editable,
)

User = get_user_model()
USER_VIEW = f"{User._meta.app_label}.view_{User._meta.model_name}"
USER_ADD = f"{User._meta.app_label}.add_{User._meta.model_name}"
USER_CHANGE = f"{User._meta.app_label}.change_{User._meta.model_name}"


def staff_permission(permission):
    def decorate(handler):
        @login_required(login_url="/mycelium/login")
        @wraps(handler)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_active or not request.user.is_staff or not request.user.has_perm(permission):
                raise PermissionDenied("Staff access and the corresponding permission are required.")
            return handler(request, *args, **kwargs)
        return wrapped
    return decorate


def require_permission(actor, permission):
    if not actor.has_perm(permission):
        raise PermissionDenied("You do not have permission for this administrative action.")


def identifier(value):
    try:
        value = int(value)
    except (TypeError, ValueError) as error:
        raise Http404("Choose an existing user or group.") from error
    if value < 1:
        raise Http404("Choose an existing user or group.")
    return value


def user_response(request, *, selected=None, form=None, notice="", status=200):
    can_change = request.user.has_perm(USER_CHANGE)
    can_add = request.user.has_perm(USER_ADD)
    users = list(User.objects.prefetch_related("groups__permissions__content_type", "user_permissions__content_type").order_by("username"))
    for user in users:
        user.admin_editable = can_change and user_editable(request.user, user)
    editable = can_change and (selected is None or user_editable(request.user, selected))
    if selected is not None and not editable or selected is None and not can_add:
        form = None
    elif form is None:
        form = (AdministrativeUserEditForm(instance=selected, actor=request.user) if selected
                else AdministrativeUserCreateForm(actor=request.user))
    response = render_page(request, "mycelium/settings_users.jinja2", {
        "users": users, "selected_user": selected, "user_form": form,
        "can_add": can_add, "can_change": can_change, "notice": notice,
    }, status=status)
    response["Cache-Control"] = "private, no-store"
    if request.method == "POST":
        response["HX-Push-Url"] = "false"
    return response


def group_response(request, *, selected=None, form=None, notice="", status=200):
    can_change = request.user.has_perm("auth.change_group")
    can_add = request.user.has_perm("auth.add_group")
    groups = list(Group.objects.prefetch_related("permissions__content_type").order_by("name"))
    for group in groups:
        group.admin_editable = can_change and group_assignable(request.user, group)
    editable = can_change and (selected is None or group_assignable(request.user, selected))
    if selected is not None and not editable or selected is None and not can_add:
        form = None
    elif form is None:
        form = AdministrativeGroupForm(instance=selected, actor=request.user)
    response = render_page(request, "mycelium/settings_groups.jinja2", {
        "groups": groups, "selected_group": selected, "group_form": form,
        "can_add": can_add, "can_change": can_change, "notice": notice,
    }, status=status)
    response["Cache-Control"] = "private, no-store"
    if request.method == "POST":
        response["HX-Push-Url"] = "false"
    return response


def administration_saved(request, kind, target, notice, *, created=False):
    import logging
    logging.getLogger('cadevil.security').info('Account administration completed', extra={
        'event': 'account_administration', 'operation': kind, 'outcome': 'completed'})
    if request.headers.get("HX-Request") == "true":
        render = user_response if kind == "users" else group_response
        return render(request, selected=target, notice=notice, status=201 if created else 200)
    messages.success(request, notice)
    query = "user" if kind == "users" else "group"
    return HttpResponseRedirect(f"/mycelium/settings/{kind}?{query}={target.pk}")


def guard_user_state(actor, target, *, was_active_superuser, active_superusers):
    if actor.pk == target.pk and not target.is_active:
        return "You cannot deactivate your own account."
    if was_active_superuser and not (target.is_active and target.is_superuser) and len(active_superusers) <= 1:
        return "At least one active superuser must remain."
    return ""


@staff_permission(USER_VIEW)
def admin_users(request):
    if request.method != "POST":
        target = get_object_or_404(User, pk=identifier(request.GET["user"])) if request.GET.get("user") else None
        return user_response(request, selected=target)
    action = request.POST.get("action")
    if action not in {"create", "update", "activate", "deactivate"}:
        raise Http404("Unknown user administration action.")
    require_permission(request.user, USER_ADD if action == "create" else USER_CHANGE)
    with transaction.atomic():
        active_superusers = list(User.objects.select_for_update().filter(is_active=True, is_superuser=True).order_by("pk"))
        target = None if action == "create" else get_object_or_404(User.objects.select_for_update(), pk=identifier(request.POST.get("user_id")))
        if target is not None and not user_editable(request.user, target):
            raise PermissionDenied("Only a superuser may change privileged accounts.")
        was_active_superuser = bool(target and target.is_active and target.is_superuser)
        if action in {"activate", "deactivate"}:
            target.is_active = action == "activate"
            error = guard_user_state(request.user, target, was_active_superuser=was_active_superuser, active_superusers=active_superusers)
            if error:
                target.refresh_from_db()
                return user_response(request, selected=target, notice=error, status=409)
            target.save(update_fields=["is_active"])
        else:
            form = (AdministrativeUserCreateForm(request.POST, actor=request.user) if action == "create"
                    else AdministrativeUserEditForm(request.POST, instance=target, actor=request.user))
            if not form.is_valid():
                return user_response(request, selected=target, form=form, status=400)
            selected_groups = list(form.cleaned_data["groups"].select_for_update())
            if any(not group_assignable(request.user, group) for group in selected_groups):
                form.add_error("groups", "A group's permissions changed. Refresh and choose groups within your access.")
                return user_response(request, selected=target, form=form, status=400)
            if action == "create":
                personal_group = Group.objects.select_for_update().filter(name=f"user_{form.cleaned_data['username']}").first()
                if personal_group and not group_assignable(request.user, personal_group):
                    form.add_error("username", "This username is reserved by a protected group. Ask a superuser to create the account.")
                    return user_response(request, form=form, status=400)
            error = guard_user_state(request.user, form.instance, was_active_superuser=was_active_superuser, active_superusers=active_superusers)
            if error:
                form.add_error(None, error)
                return user_response(request, selected=target, form=form, status=409)
            try:
                with transaction.atomic():
                    target = form.save()
                    if action == "create":
                        personal_group = Group.objects.filter(name=f"user_{target.username}").first()
                        if personal_group and group_assignable(request.user, personal_group):
                            target.groups.add(personal_group)
            except IntegrityError:
                form.add_error(None, "This username was registered by another request. Choose another username.")
                return user_response(request, selected=None if action == "create" else target, form=form, status=409)
    return administration_saved(request, "users", target, "User created." if action == "create" else "User settings updated.", created=action == "create")


@staff_permission("auth.view_group")
def admin_groups(request):
    if request.method != "POST":
        target = get_object_or_404(Group, pk=identifier(request.GET["group"])) if request.GET.get("group") else None
        return group_response(request, selected=target)
    action = request.POST.get("action")
    if action not in {"create", "update"}:
        raise Http404("Unknown group administration action.")
    require_permission(request.user, "auth.add_group" if action == "create" else "auth.change_group")
    with transaction.atomic():
        target = None if action == "create" else get_object_or_404(Group.objects.select_for_update(), pk=identifier(request.POST.get("group_id")))
        if target is not None and not group_assignable(request.user, target):
            raise PermissionDenied("Only a superuser may change privileged groups.")
        form = AdministrativeGroupForm(request.POST, instance=target, actor=request.user)
        if not form.is_valid():
            return group_response(request, selected=target, form=form, status=400)
        try:
            with transaction.atomic():
                target = form.save()
        except IntegrityError:
            form.add_error("name", "This group name was registered by another request. Choose another name.")
            return group_response(request, selected=target, form=form, status=409)
    return administration_saved(request, "groups", target, "Group created." if action == "create" else "Group settings updated.", created=action == "create")
