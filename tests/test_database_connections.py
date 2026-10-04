"""Exercise connection boundaries through Bolt's actual middleware chain."""
import asyncio
import threading
from unittest.mock import patch

from asgiref.sync import async_to_sync, sync_to_async
from django.db import DatabaseError
from django.http import JsonResponse
from django.test import SimpleTestCase, override_settings
from django_bolt import AllowAny, BoltAPI, Request
from django_bolt.testing import TestClient

from apps.shared.bolt_pages import page_endpoint
from apps.shared.database_connections import DatabaseConnectionMiddleware
from apps.shared.request_logging import configure_api_logging


@override_settings(DATABASE_CONNECTION_LIFECYCLE=True, ALLOWED_HOSTS=['testserver'])
class DatabaseConnectionTests(SimpleTestCase):
    def test_response_and_handler_failure_cleanup_in_the_orm_thread(self):
        events = []
        api = BoltAPI(django_middleware=True, enable_logging=False)
        configure_api_logging(api)

        @api.get('/connection/{outcome}', guards=[AllowAny()])
        @page_endpoint
        def handler(request: Request, outcome: str):
            events.append(('handler', threading.get_ident()))
            if outcome == 'error':
                raise DatabaseError('connection unavailable')
            return JsonResponse({'status': 'ok'})

        with patch('apps.shared.database_connections.close_old_connections',
                   side_effect=lambda: events.append(('close', threading.get_ident()))):
            with TestClient(api, base_url='http://testserver', raise_server_exceptions=False) as client:
                self.assertEqual(client.get('/connection/ok').status_code, 200)
                self.assertEqual(client.get('/connection/error').status_code, 500)
        self.assertEqual([event for event, _ in events], ['close', 'handler', 'close'] * 2)
        self.assertEqual(len({thread for _, thread in events}), 1)

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_early_security_response_still_closes_connections(self):
        api = BoltAPI(django_middleware=['django.middleware.security.SecurityMiddleware'])
        configure_api_logging(api)

        @api.get('/secure', guards=[AllowAny()])
        async def handler(request: Request):
            raise AssertionError('Security middleware should return first')

        with patch('apps.shared.database_connections.close_old_connections') as cleanup:
            with TestClient(api, base_url='http://testserver') as client:
                response = client.get('/secure', follow_redirects=False)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(cleanup.call_count, 2)

    def test_async_handlers_remain_concurrent_and_cancellation_closes_connections(self):
        async def run():
            entered = 0
            ready = asyncio.Event()

            async def handler(request):
                nonlocal entered
                entered += 1
                if entered == 2:
                    ready.set()
                await asyncio.wait_for(ready.wait(), 2)

            middleware = DatabaseConnectionMiddleware(handler)
            await asyncio.gather(middleware(None), middleware(None))

            async def cancelled(request):
                raise asyncio.CancelledError

            with self.assertRaises(asyncio.CancelledError):
                await DatabaseConnectionMiddleware(cancelled)(None)

        with patch('apps.shared.database_connections.close_old_connections') as cleanup:
            async_to_sync(run)()
        self.assertEqual(cleanup.call_count, 6)

    def test_websocket_permission_errors_close_in_the_same_thread(self):
        from apps.shared.admin_logs import staff_session
        events = []
        with patch('apps.shared.admin_logs.close_old_connections',
                   side_effect=lambda: events.append(threading.get_ident())), \
                patch('apps.shared.admin_logs.get_user', side_effect=DatabaseError('unavailable')):
            with self.assertRaises(DatabaseError):
                async_to_sync(sync_to_async(staff_session, thread_sensitive=True))({})
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0], events[1])

    @override_settings(DATABASE_CONNECTION_LIFECYCLE=False)
    def test_connection_wrapper_is_optional_outside_container_settings(self):
        api = BoltAPI(enable_logging=False)
        configure_api_logging(api)
        self.assertNotIn(DatabaseConnectionMiddleware, api._middleware)
