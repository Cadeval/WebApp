import asyncio
import logging
import tempfile
from pathlib import Path
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.auth.context_processors import PermWrapper
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import TestCase, override_settings, Client
from django_bolt.testing import WebSocketTestClient
from config.api import api
from tests.bolt_browser import BoltBrowser
from shared.live_logs import SharedLogHandler, read_logs

class LiveLogTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        setting = override_settings(ADMIN_LOG_ENABLED=True, ADMIN_LOG_PATH=Path(self.temp.name)/'logs.sqlite3')
        setting.enable(); self.addCleanup(setting.disable)
        self.admin = get_user_model().objects.create_user(username='log-admin', is_staff=True)
        self.regular = get_user_model().objects.create_user(username='log-user')
        self.staff_without_permission = get_user_model().objects.create_user(username='log-staff', is_staff=True)
        self.inactive_admin = get_user_model().objects.create_user(username='log-inactive', is_staff=True, is_active=False)
        self.permission = Permission.objects.get(codename='view_application_logs')
        self.admin.user_permissions.add(self.permission)
        self.inactive_admin.user_permissions.add(self.permission)
        from django_bolt import BoltAPI
        from mycelium.api import api as home_api
        combined = BoltAPI(trailing_slash='keep'); combined.mount('',api); combined.mount('',home_api)
        self.client = BoltBrowser(api=combined); self.addCleanup(self.client.close)

    def cookie(self, user):
        client = Client(); client.force_login(user)
        return 'sessionid=' + client.cookies['sessionid'].value

    def test_shell_fragment_history_and_admin_access(self):
        self.client.force_login(self.admin)
        for path in ['/', '/mycelium/settings', '/mycelium/settings?section=access', '/admin/logs/']:
            full = self.client.get(path); self.assertEqual(full.status_code,200)
            self.assertContains(full,'<html');self.assertContains(full,'id="content-container"',count=1)
            fragment = self.client.get(path,HTTP_HX_REQUEST='true')
            self.assertEqual(fragment.status_code,200);self.assertNotContains(fragment,'<html')
            self.assertContains(fragment,'id="content-container"',count=1)
            restored = self.client.get(path,HTTP_HX_REQUEST='true',HTTP_HX_HISTORY_RESTORE_REQUEST='true')
            self.assertContains(restored,'<html');self.assertContains(restored,'id="content-container"',count=1)
        for path in ['/mycelium/user', '/mycelium/profile']:
            redirected = self.client.get(path)
            self.assertEqual(redirected.status_code,302)
            self.assertEqual(redirected.url,'/mycelium/settings')
        self.assertEqual(self.client.get('/admin/logs/')['Cache-Control'],'no-store')
        self.assertEqual(self.client.get('/admin/logs/',HTTP_HX_REQUEST='true',HTTP_HX_HISTORY_RESTORE_REQUEST='true')['Cache-Control'],'no-store')
        self.client.force_login(self.regular);self.assertEqual(self.client.get('/admin/logs/').status_code,403)
        self.admin.user_permissions.clear();self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/admin/logs/').status_code,403)
        self.client.logout();self.assertEqual(self.client.get('/admin/logs/').status_code,302)
        self.assertContains(self.client.get('/mycelium/login',HTTP_HX_REQUEST='true'),'id="content-container"',count=1)

    def test_popover_markup_requires_active_staff_and_explicit_permission(self):
        # Rendering the cheap persistent UI does not itself read or subscribe to logs.
        from unittest.mock import patch
        with patch('shared.admin_logs.read_logs', side_effect=AssertionError('Template read logs')):
            allowed = render_to_string('shared/admin_logs_popover.html', {
                'user': self.admin, 'perms': PermWrapper(self.admin),
            })
        self.assertIn('id="admin-logs-popover"', allowed)
        self.assertIn('popover="auto"', allowed)
        self.assertIn('role="dialog"', allowed)
        self.assertIn('popovertargetaction="hide"', allowed)
        self.assertIn('aria-pressed="false"', allowed)
        self.assertIn('data-log-connecting', allowed)
        self.regular.user_permissions.add(self.permission)
        for user in [AnonymousUser(), self.regular, self.staff_without_permission, self.inactive_admin]:
            denied = render_to_string('shared/admin_logs_popover.html', {
                'user': user, 'perms': PermWrapper(user),
            })
            self.assertNotIn('data-admin-logs', denied)
            self.assertNotIn('/ws/logs', denied)

    def test_header_opens_persistent_popover_without_navigation(self):
        self.client.force_login(self.admin)
        home = self.client.get('/')
        self.assertContains(home, 'id="admin-logs-popover"', count=1)
        self.assertContains(home, 'popovertarget="admin-logs-popover"')
        self.assertNotContains(home, 'hx-get="/admin/logs/"')
        fragment = self.client.get('/', HTTP_HX_REQUEST='true')
        self.assertNotContains(fragment, 'id="admin-logs-popover"')
        fallback = self.client.get('/admin/logs/', HTTP_HX_REQUEST='true')
        self.assertContains(fallback, 'Open live logs')
        self.assertContains(fallback, 'popovertarget="admin-logs-popover"')
        self.assertNotContains(fallback, 'data-admin-logs')
        for user in [self.regular, self.staff_without_permission]:
            self.client.force_login(user)
            self.assertNotContains(self.client.get('/'), 'admin-logs-popover')
        self.client.logout()
        self.assertNotContains(self.client.get('/'), 'admin-logs-popover')

    def test_bounded_log_store_cross_handler_and_cursor(self):
        from unittest.mock import patch
        handler = SharedLogHandler()
        with patch('shared.live_logs.LIMIT',3):
            for i in range(5):handler.emit(logging.LogRecord('application',logging.INFO,'',1,'Entry %s',(i,),None))
        rows=read_logs();self.assertEqual([r['message'] for r in rows],['Entry 2','Entry 3','Entry 4'])
        self.assertEqual(read_logs(rows[-1]['id']),[])
        SharedLogHandler().emit(logging.LogRecord('worker-two',logging.WARNING,'',1,'Live update',(),None))
        self.assertEqual(read_logs(rows[-1]['id'])[0]['logger'],'worker-two')

    def test_socket_auth_origin_stream_and_revocation(self):
        self.regular.user_permissions.add(self.permission)
        admin_cookie=self.cookie(self.admin);regular_cookie=self.cookie(self.regular)
        staff_cookie=self.cookie(self.staff_without_permission);inactive_cookie=self.cookie(self.inactive_admin)
        async def run():
            # Denied sockets never accept or expose any log entries.
            from shared.admin_logs import log_stream
            class Denied:
                cookies={};headers={'host':'testserver','origin':'http://testserver'}
                async def close(self,code):self.code=code
                async def accept(self):raise AssertionError('Denied socket accepted')
            for cookie,origin in [('', 'http://testserver'),(regular_cookie,'http://testserver'),(staff_cookie,'http://testserver'),(inactive_cookie,'http://testserver'),(admin_cookie,'http://foreign.example')]:
                socket=Denied();socket.cookies={'sessionid':cookie.split('=',1)[-1]} if cookie else {};socket.headers={'host':'testserver','origin':origin}
                await log_stream(socket);self.assertEqual(socket.code,4403)
            headers={'host':'testserver','origin':'http://testserver','cookie':admin_cookie}
            async with WebSocketTestClient(api,'/ws/logs',headers=headers,cors_allowed_origins=['http://testserver']) as socket:
                initial=await socket.receive_json();self.assertEqual(initial['type'],'logs')
                handler=SharedLogHandler();handler.emit(logging.LogRecord('worker',logging.INFO,'',1,'Streaming check',(),None))
                update=await socket.receive_json();self.assertTrue(any(r['message']=='Streaming check' for r in update['entries']))
                await sync_to_async(self.admin.user_permissions.clear)()
                message=await socket.receive();self.assertEqual(message['type'],'websocket.close');self.assertEqual(message['code'],4403)
        asyncio.run(run())
