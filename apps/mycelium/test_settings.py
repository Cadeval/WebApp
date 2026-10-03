"""Personal settings use the native Bolt/session/CSRF browser path."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django_bolt import BoltAPI

from apps.plugin_manager.api import api as plugin_api
from config.api import api as main_api
from tests.bolt_browser import BoltBrowser
from .api import api as home_api
from .settings_forms import ProfileSettingsForm


class UserSettingsTests(TestCase):
    def setUp(self):
        combined = BoltAPI(trailing_slash="keep", django_middleware=True)
        for api in (main_api, home_api, plugin_api):
            combined.mount("", api)
        self.browser_api = combined
        self.client = BoltBrowser(api=combined)
        self.addCleanup(self.client.close)
        self.user = get_user_model().objects.create_user(
            username="settings-user", password="Original-settings-secret-812", max_calculations=3, active_calculations=2
        )
        self.other = get_user_model().objects.create_user(username="settings-other")
        self.client.force_login(self.user)

    def profile(self, **changes):
        return {"action": "profile", "first_name": "Mia", "last_name": "Example", "email": "mia@example.test", "theme": "dark", **changes}

    def test_full_and_fragment_settings_have_one_content_boundary(self):
        full = self.client.get("/mycelium/settings")
        self.assertContains(full, "User settings")
        self.assertEqual(full["Cache-Control"], "private, no-store")
        self.assertContains(full, "<html")
        self.assertContains(full, 'id="content-container"', count=1)
        fragment = self.client.get("/mycelium/settings", HTTP_HX_REQUEST="true")
        self.assertEqual(fragment["Cache-Control"], "private, no-store")
        self.assertNotContains(fragment, "<html")
        self.assertContains(fragment, 'id="content-container"', count=1)

    def test_security_challenges_are_fresh_and_all_sensitive_responses_are_uncached(self):
        first = self.client.get("/mycelium/settings?section=security")
        second = self.client.get("/mycelium/settings?section=security", HTTP_HX_REQUEST="true")
        self.assertEqual(first["Cache-Control"], "private, no-store")
        self.assertEqual(second["Cache-Control"], "private, no-store")
        self.assertNotEqual(first.context["registration"]["challenge"], second.context["registration"]["challenge"])
        self.assertEqual(second.context["registration"]["owner"], str(self.user.pk))
        invalid = self.client.post("/mycelium/settings", {
            "action": "password", "old_password": "wrong-old-password",
            "new_password1": "Different-settings-secret-917", "new_password2": "Different-settings-secret-917",
        }, HTTP_HX_REQUEST="true")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid["Cache-Control"], "private, no-store")

    def test_legacy_profile_links_forward_to_canonical_settings(self):
        for route in ("/mycelium/user", "/mycelium/profile", "/mycelium/user/profile"):
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.url, "/mycelium/settings")

    def test_anonymous_settings_redirects_to_login(self):
        self.client.logout()
        response = self.client.get("/mycelium/settings")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/mycelium/login"))

    def test_profile_saves_only_current_users_public_fields_and_theme(self):
        response = self.client.post("/mycelium/settings", self.profile(
            user_id=self.other.pk, id=self.other.pk, username="forged-username",
            is_staff="on", is_superuser="on", max_calculations="999", groups=[self.other.groups.get().pk],
        ), HTTP_HX_REQUEST="true")
        self.assertContains(response, "Account settings saved.")
        self.assertEqual(response["HX-Push-Url"], "false")
        self.user.refresh_from_db(); self.other.refresh_from_db()
        self.assertEqual((self.user.first_name, self.user.last_name, self.user.email, self.user.theme),
                         ("Mia", "Example", "mia@example.test", "dark"))
        self.assertEqual(self.user.username, "settings-user")
        self.assertFalse(self.user.is_staff); self.assertFalse(self.user.is_superuser)
        self.assertEqual(self.user.max_calculations, 3)
        self.assertEqual(self.user.active_calculations, 2)
        self.assertEqual(self.other.first_name, "")
        self.assertNotIn(self.other.groups.get(), self.user.groups.all())
        self.assertEqual(self.client.get("/mycelium/settings").context["profile_form"].initial["theme"], "dark")

    def test_stale_profile_form_does_not_overwrite_concurrent_roles_or_capacity(self):
        form = ProfileSettingsForm(self.profile(), instance=self.user)
        self.assertTrue(form.is_valid())
        get_user_model().objects.filter(pk=self.user.pk).update(is_staff=True, max_calculations=8, active_calculations=4)
        form.save()
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_staff)
        self.assertEqual((self.user.max_calculations, self.user.active_calculations), (8, 4))
        self.assertEqual(self.user.theme, "dark")

    def test_invalid_profile_and_missing_csrf_do_not_change_account(self):
        response = self.client.post("/mycelium/settings", self.profile(email="invalid", theme="unknown"), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "")
        self.assertEqual(self.user.theme, "auto")
        self.client.auto_csrf = False
        self.assertEqual(self.client.post("/mycelium/settings", self.profile()).status_code, 403)
        self.user.refresh_from_db(); self.assertEqual(self.user.first_name, "")

    def test_password_change_keeps_this_session_and_invalidates_other_sessions(self):
        other_session = BoltBrowser(api=self.browser_api)
        self.addCleanup(other_session.close)
        other_session.force_login(self.user)
        self.assertEqual(other_session.get("/mycelium/settings").status_code, 200)
        response = self.client.post("/mycelium/settings", {
            "action": "password", "old_password": "Original-settings-secret-812",
            "new_password1": "Updated-settings-secret-913", "new_password2": "Updated-settings-secret-913",
        }, HTTP_HX_REQUEST="true")
        self.assertContains(response, "Password changed.")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Updated-settings-secret-913"))
        self.assertEqual(self.client.get("/mycelium/settings").status_code, 200)
        self.assertEqual(other_session.get("/mycelium/settings").status_code, 302)

    def test_wrong_old_password_or_mismatched_confirmation_cannot_change_password(self):
        for old, confirmation in (("wrong-old-password", "Next-secret-914"), ("Original-settings-secret-812", "mismatch")):
            response = self.client.post("/mycelium/settings", {
                "action": "password", "old_password": old,
                "new_password1": "Next-secret-914", "new_password2": confirmation,
            }, HTTP_HX_REQUEST="true")
            self.assertEqual(response.status_code, 400)
            self.user.refresh_from_db()
            self.assertTrue(self.user.check_password("Original-settings-secret-812"))

    def test_access_page_is_read_only_and_unknown_actions_cannot_change_privileges(self):
        response = self.client.get("/mycelium/settings?section=access")
        self.assertContains(response, "Calculation capacity")
        self.assertEqual(response.context["capacity"], {"active": 2, "limit": 3})
        self.assertNotContains(response, "Manage users")
        self.assertNotContains(response, "Manage groups")
        self.assertEqual(self.client.post("/mycelium/settings", {"action": "deactivate"}).status_code, 404)
        self.user.refresh_from_db(); self.assertTrue(self.user.is_active)
        self.assertEqual(self.client.get("/mycelium/settings?section=missing").context["section"], "account")
