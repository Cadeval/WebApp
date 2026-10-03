"""Administrative changes cannot escape staff permissions or role boundaries."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django_bolt import BoltAPI

from apps.plugin_manager.api import api as plugin_api
from config.api import api as main_api
from tests.bolt_browser import BoltBrowser
from .api import api as home_api


class UserAndGroupAdministrationTests(TestCase):
    def setUp(self):
        combined = BoltAPI(trailing_slash="keep", django_middleware=True)
        for api in (main_api, home_api, plugin_api):
            combined.mount("", api)
        self.client = BoltBrowser(api=combined)
        self.addCleanup(self.client.close)
        User = get_user_model()
        self.superuser = User.objects.create_superuser(username="account-root", password="Admin-test-password-814")
        self.delegated = User.objects.create_user(username="account-delegated", is_staff=True)
        self.staff = User.objects.create_user(username="account-staff", is_staff=True)
        self.target = User.objects.create_user(username="account-target", max_calculations=2, active_calculations=1)
        self.owned_permission = self.permission("shared", "view_application_logs")
        self.foreign_permission = self.permission("shared", "change_buildingmetrics")
        for app, code in (("shared", "view_cadeviluser"), ("shared", "add_cadeviluser"), ("shared", "change_cadeviluser"),
                          ("auth", "view_group"), ("auth", "add_group"), ("auth", "change_group")):
            self.delegated.user_permissions.add(self.permission(app, code))
        self.delegated.user_permissions.add(self.owned_permission)
        self.normal_group = Group.objects.create(name="Account readers")
        self.normal_group.permissions.add(self.owned_permission)
        self.privileged_group = Group.objects.create(name="Account administrators")
        self.privileged_group.permissions.add(self.permission("shared", "change_cadeviluser"))
        self.login(self.superuser)

    def permission(self, app, code):
        return Permission.objects.get(content_type__app_label=app, codename=code)

    def login(self, user):
        self.client.logout(); self.client.force_login(user)
        self.client.get("/mycelium/settings")

    def user_payload(self, target=None, **changes):
        data = {"action": "update" if target else "create", "username": target.username if target else "created-account",
                "first_name": "Created", "last_name": "User", "email": "created@example.test",
                "max_calculations": "4", "groups": [self.normal_group.pk], "is_active": "on"}
        if target:
            data["user_id"] = target.pk
            if target.is_staff: data["is_staff"] = "on"
            if target.is_superuser: data["is_superuser"] = "on"
        else:
            data.update(password1="Created-account-password-815", password2="Created-account-password-815")
        data.update(changes)
        return data

    def users_post(self, data):
        return self.client.post("/mycelium/settings/users", data, HTTP_HX_REQUEST="true")

    def groups_post(self, data):
        return self.client.post("/mycelium/settings/groups", data, HTTP_HX_REQUEST="true")

    def test_staff_flag_and_view_permission_are_both_required(self):
        for user in (self.staff, self.target):
            self.login(user)
            for route in ("/mycelium/settings/users", "/mycelium/settings/groups"):
                self.assertEqual(self.client.get(route).status_code, 403)
        self.target.user_permissions.add(self.permission("shared", "view_cadeviluser"))
        self.login(self.target)
        self.assertEqual(self.client.get("/mycelium/settings/users").status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get("/mycelium/settings/users").status_code, 302)

    def test_admin_pages_keep_full_and_fragment_boundaries(self):
        for route in ("/mycelium/settings/users", "/mycelium/settings/groups"):
            full = self.client.get(route)
            self.assertEqual(full["Cache-Control"], "private, no-store")
            self.assertContains(full, "<html")
            self.assertContains(full, 'id="content-container"', count=1)
            fragment = self.client.get(route, HTTP_HX_REQUEST="true")
            self.assertEqual(fragment["Cache-Control"], "private, no-store")
            self.assertNotContains(fragment, "<html")
            self.assertContains(fragment, 'id="content-container"', count=1)

    def test_create_user_hashes_password_assigns_groups_and_capacity(self):
        self.login(self.delegated)
        response = self.users_post(self.user_payload())
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        user = get_user_model().objects.get(username="created-account")
        self.assertTrue(user.check_password("Created-account-password-815"))
        self.assertNotEqual(user.password, "Created-account-password-815")
        self.assertEqual(user.max_calculations, 4)
        self.assertIn(self.normal_group, user.groups.all())
        self.assertIn(Group.objects.get(name="user_created-account"), user.groups.all())
        self.assertFalse(user.is_staff); self.assertFalse(user.is_superuser)

    def test_create_or_update_requires_its_own_permission(self):
        self.delegated.user_permissions.remove(self.permission("shared", "add_cadeviluser"))
        self.login(self.delegated)
        self.assertEqual(self.users_post(self.user_payload()).status_code, 403)
        self.delegated.user_permissions.remove(self.permission("shared", "change_cadeviluser"))
        self.login(self.delegated)
        self.assertEqual(self.users_post(self.user_payload(self.target)).status_code, 403)
        self.assertEqual(self.users_post({"action": "deactivate", "user_id": self.target.pk}).status_code, 403)
        self.assertFalse(get_user_model().objects.filter(username="created-account").exists())

    def test_delegated_admin_cannot_grant_staff_superuser_or_privileged_groups(self):
        self.login(self.delegated)
        for changes in ({"is_staff": "on"}, {"is_staff": "on", "is_superuser": "on"}, {"groups": [self.privileged_group.pk]}):
            self.assertEqual(self.users_post(self.user_payload(**changes)).status_code, 400)
            self.assertFalse(get_user_model().objects.filter(username="created-account").exists())

    def test_superuser_can_create_staff_and_assign_administrative_group(self):
        response = self.users_post(self.user_payload(is_staff="on", groups=[self.privileged_group.pk]))
        self.assertEqual(response.status_code, 201)
        user = get_user_model().objects.get(username="created-account")
        self.assertTrue(user.is_staff)
        self.assertIn(self.privileged_group, user.groups.all())

    def test_reserved_personal_group_cannot_silently_grant_privileged_membership(self):
        group = Group.objects.create(name="user_created-account")
        group.permissions.add(self.permission("shared", "change_cadeviluser"))
        self.login(self.delegated)
        response = self.users_post(self.user_payload())
        self.assertEqual(response.status_code, 400)
        self.assertFalse(get_user_model().objects.filter(username="created-account").exists())

    def test_username_length_leaves_room_for_automatic_group_prefix(self):
        response = self.users_post(self.user_payload(username="x" * 146))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(get_user_model().objects.filter(username="x" * 146).exists())

    def test_delegated_admin_cannot_edit_privileged_accounts_or_groups(self):
        self.login(self.delegated)
        for target in (self.superuser, self.staff):
            self.assertEqual(self.users_post(self.user_payload(target)).status_code, 403)
            page = self.client.get(f"/mycelium/settings/users?user={target.pk}")
            self.assertEqual(page.status_code, 200)
            self.assertIsNone(page.context["user_form"])
        self.assertEqual(self.groups_post({"action": "update", "group_id": self.privileged_group.pk,
                                          "name": "Forged privileged rename", "permissions": []}).status_code, 403)
        self.privileged_group.refresh_from_db(); self.assertEqual(self.privileged_group.name, "Account administrators")

    def test_edit_user_preserves_running_count_and_direct_permissions(self):
        self.target.user_permissions.add(self.owned_permission)
        self.login(self.delegated)
        response = self.users_post(self.user_payload(self.target))
        self.assertEqual(response.status_code, 200)
        self.target.refresh_from_db()
        self.assertEqual(self.target.max_calculations, 4)
        self.assertEqual(self.target.active_calculations, 1)
        self.assertIn(self.owned_permission, self.target.user_permissions.all())

    def test_capacity_bounds_and_username_uniqueness_leave_user_unchanged(self):
        for limit in ("-1", "10001", "invalid"):
            response = self.users_post(self.user_payload(self.target, max_calculations=limit))
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response["Cache-Control"], "private, no-store")
            self.target.refresh_from_db(); self.assertEqual(self.target.max_calculations, 2)
        self.assertEqual(self.users_post(self.user_payload(self.target, username=self.superuser.username)).status_code, 400)
        self.target.refresh_from_db(); self.assertEqual(self.target.username, "account-target")

    def test_deactivation_is_reversible_and_preserves_memberships(self):
        self.target.groups.add(self.normal_group)
        self.login(self.delegated)
        self.assertEqual(self.users_post({"action": "deactivate", "user_id": self.target.pk}).status_code, 200)
        self.target.refresh_from_db(); self.assertFalse(self.target.is_active)
        self.assertIn(self.normal_group, self.target.groups.all())
        self.assertEqual(self.users_post({"action": "activate", "user_id": self.target.pk}).status_code, 200)
        self.target.refresh_from_db(); self.assertTrue(self.target.is_active)

    def test_self_deactivation_and_last_superuser_demotion_are_blocked(self):
        self.assertEqual(self.users_post({"action": "deactivate", "user_id": self.superuser.pk}).status_code, 409)
        self.assertEqual(self.users_post(self.user_payload(self.superuser, is_superuser="", groups=[])).status_code, 409)
        self.superuser.refresh_from_db()
        self.assertTrue(self.superuser.is_active); self.assertTrue(self.superuser.is_superuser)

    def test_another_active_superuser_allows_reversible_superuser_deactivation(self):
        second = get_user_model().objects.create_superuser(username="account-second-root", password="Second-root-password-816")
        self.login(second)
        self.assertEqual(self.users_post({"action": "deactivate", "user_id": self.superuser.pk}).status_code, 200)
        self.superuser.refresh_from_db(); self.assertFalse(self.superuser.is_active)
        self.assertTrue(get_user_model().objects.filter(pk=self.superuser.pk).exists())

    def test_group_create_rename_and_permission_assignment_use_owned_permissions(self):
        self.login(self.delegated)
        response = self.groups_post({"action": "create", "name": "Delegated report readers", "permissions": [self.owned_permission.pk]})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        group = Group.objects.get(name="Delegated report readers")
        self.assertEqual(set(group.permissions.all()), {self.owned_permission})
        response = self.groups_post({"action": "update", "group_id": group.pk, "name": "Renamed report readers", "permissions": []})
        self.assertEqual(response.status_code, 200)
        group.refresh_from_db(); self.assertEqual(group.name, "Renamed report readers")
        self.assertFalse(group.permissions.exists())

    def test_delegated_group_assignment_rejects_unheld_and_administrative_permissions(self):
        self.login(self.delegated)
        for permission in (self.foreign_permission, self.permission("shared", "change_cadeviluser")):
            self.assertEqual(self.groups_post({"action": "create", "name": "Forged permission group", "permissions": [permission.pk]}).status_code, 400)
            self.assertFalse(Group.objects.filter(name="Forged permission group").exists())

    def test_group_add_and_change_require_separate_permissions(self):
        self.delegated.user_permissions.remove(self.permission("auth", "add_group"), self.permission("auth", "change_group"))
        self.login(self.delegated)
        self.assertEqual(self.groups_post({"action": "create", "name": "Unauthorized group"}).status_code, 403)
        self.assertEqual(self.groups_post({"action": "update", "group_id": self.normal_group.pk, "name": "Unauthorized rename"}).status_code, 403)

    def test_view_only_admin_gets_no_mutation_forms(self):
        self.staff.user_permissions.add(self.permission("shared", "view_cadeviluser"), self.permission("auth", "view_group"))
        self.login(self.staff)
        users = self.client.get("/mycelium/settings/users")
        groups = self.client.get("/mycelium/settings/groups")
        self.assertIsNone(users.context["user_form"]); self.assertIsNone(groups.context["group_form"])
        self.assertContains(users, "View only")
        self.assertContains(groups, "View only")
        self.assertNotContains(users, 'name="action"')
        self.assertNotContains(groups, 'name="action"')

    def test_delete_actions_and_malformed_identifiers_never_remove_records(self):
        self.assertEqual(self.users_post({"action": "delete", "user_id": self.target.pk}).status_code, 404)
        self.assertEqual(self.groups_post({"action": "delete", "group_id": self.normal_group.pk}).status_code, 404)
        self.assertTrue(get_user_model().objects.filter(pk=self.target.pk).exists())
        self.assertTrue(Group.objects.filter(pk=self.normal_group.pk).exists())
        for route in ("/mycelium/settings/users?user=invalid", "/mycelium/settings/groups?group=invalid"):
            self.assertEqual(self.client.get(route).status_code, 404)

    def test_administration_requires_csrf(self):
        self.client.auto_csrf = False
        self.assertEqual(self.users_post({"action": "deactivate", "user_id": self.target.pk}).status_code, 403)
        self.assertEqual(self.groups_post({"action": "create", "name": "Missing token"}).status_code, 403)
        self.target.refresh_from_db(); self.assertTrue(self.target.is_active)
