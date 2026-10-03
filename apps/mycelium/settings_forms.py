"""Account forms and restricted administrative role assignment."""
from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group, Permission

User = get_user_model()


def permission_name(permission):
    return f"{permission.content_type.app_label}.{permission.codename}"


def administrative_permission(permission):
    """Role and permission delegation itself is reserved for superusers."""
    model = permission.content_type.model
    action = permission.codename.split("_", 1)[0]
    return action in {"add", "change", "delete"} and (
        permission.content_type.app_label == "auth" and model in {"group", "permission", "user"}
        or permission.content_type.app_label == User._meta.app_label and model == User._meta.model_name
    )


def group_assignable(actor, group):
    if actor.is_superuser:
        return True
    permissions = list(group.permissions.all())
    held = actor.get_all_permissions()
    return all(not administrative_permission(permission) and permission_name(permission) in held
               for permission in permissions)


def assignable_groups(actor):
    groups = Group.objects.prefetch_related("permissions__content_type").order_by("name")
    if actor.is_superuser:
        return groups
    return Group.objects.filter(pk__in=[group.pk for group in groups if group_assignable(actor, group)]).order_by("name")


def assignable_permissions(actor):
    permissions = Permission.objects.select_related("content_type").order_by("content_type__app_label", "codename")
    if actor.is_superuser:
        return permissions
    held = actor.get_all_permissions()
    return permissions.filter(pk__in=[permission.pk for permission in permissions
                                     if permission_name(permission) in held and not administrative_permission(permission)])


def user_editable(actor, target):
    if actor.is_superuser:
        return True
    if target.is_superuser or target.is_staff:
        return False
    held = actor.get_all_permissions()
    direct = list(target.user_permissions.select_related("content_type"))
    return all(not administrative_permission(permission) and permission_name(permission) in held for permission in direct) and all(
        group_assignable(actor, group) for group in target.groups.prefetch_related("permissions__content_type")
    )


class ProfileSettingsForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "theme")

    def save(self, commit=True):
        user = super().save(commit=False)
        if commit:
            user.save(update_fields=self.Meta.fields)
        return user


class AdministrativeUserMixin:
    def __init__(self, *args, actor, **kwargs):
        self.actor = actor
        super().__init__(*args, **kwargs)
        self.fields["groups"].queryset = assignable_groups(actor)
        self.fields["max_calculations"] = forms.IntegerField(
            label="Maximum concurrent calculations", min_value=0,
            max_value=getattr(settings, "USER_MAX_CALCULATION_LIMIT", 10000),
            initial=self.instance.max_calculations if self.instance.pk else 1,
            help_text="Zero prevents starting calculations. Running calculations are retained.",
        )
        if not actor.is_superuser:
            self.fields.pop("is_staff", None)
            self.fields.pop("is_superuser", None)

    def clean(self):
        cleaned = super().clean()
        if not self.actor.is_superuser:
            for field in ("is_staff", "is_superuser"):
                if forms.BooleanField(required=False).to_python(self.data.get(field)):
                    raise forms.ValidationError("Only a superuser may grant staff or superuser access.")
        if cleaned.get("is_superuser") and not cleaned.get("is_staff"):
            raise forms.ValidationError("A superuser must also have staff access.")
        return cleaned


USER_FIELDS = ("username", "first_name", "last_name", "email", "max_calculations", "groups", "is_active", "is_staff", "is_superuser")


class AdministrativeUserCreateForm(AdministrativeUserMixin, UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = USER_FIELDS

    def clean_username(self):
        username = super().clean_username()
        limit = Group._meta.get_field("name").max_length - len("user_")
        if len(username) > limit:
            raise forms.ValidationError(f"Use at most {limit} characters so the account's automatic group has a valid name.")
        return username


class AdministrativeUserEditForm(AdministrativeUserMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = USER_FIELDS

    def save(self, commit=True):
        user = super().save(commit=False)
        if commit:
            user.save(update_fields=[name for name in self.fields if name != "groups"])
            self.save_m2m()
        return user


class AdministrativeGroupForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ("name", "permissions")

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["permissions"].queryset = assignable_permissions(actor)
