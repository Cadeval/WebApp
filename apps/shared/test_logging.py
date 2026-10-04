"""Privacy and correlation checks over the real native Bolt request transport."""
import asyncio
import json
import logging
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from django.test import SimpleTestCase, override_settings
from django.http import JsonResponse
from django_bolt import AllowAny, BoltAPI
from django_bolt.exceptions import HTTPException
from django_bolt.request import Request
from django_bolt.testing import AsyncTestClient, TestClient

from .live_logs import SharedLogHandler, install_handler, read_logs
from .bolt_pages import page_endpoint
from .request_logging import RequestLoggingMiddleware, configure_api_logging
from .logging_utils import MAX_MESSAGE_LENGTH, SafeJSONFormatter, SafeTextFormatter, request_id_context


class JSONCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.entries = []
        self.setFormatter(SafeJSONFormatter())

    def emit(self, record):
        self.entries.append(json.loads(self.format(record)))


class LoggingPrivacyTests(SimpleTestCase):
    def test_redaction_is_shared_by_console_and_persistent_messages(self):
        value = ('password="hunter-two" token=secret-token Authorization: Bearer private-bearer '
                 'Cookie: sessionid=private-cookie; csrftoken=private-csrf '
                 'https://alice:private-password@example.test/path?search=private-query '
                 '/plugins/store/?filter=private-filter /tmp/private-upload.ifc '
                 '\x1b[31m\r\nforged entry')
        record = logging.LogRecord('application', logging.WARNING, '', 1, '%s', (value,), None)
        record.body = 'private-body'
        record.user_id = 'private-user'
        record.configuration = {'password': 'private-config'}
        console = SafeJSONFormatter().format(record)
        with tempfile.TemporaryDirectory() as directory, override_settings(
                ADMIN_LOG_ENABLED=True, ADMIN_LOG_PATH=Path(directory) / 'logs.sqlite3'):
            SharedLogHandler().emit(record)
            rows = read_logs()
        live = rows[0]['message']
        for secret in ('hunter-two', 'secret-token', 'private-bearer', 'private-cookie',
                       'private-csrf', 'private-password', 'alice', 'private-query',
                       'private-filter', 'private-upload', 'private-body', 'private-user', 'private-config'):
            self.assertNotIn(secret, console)
            self.assertNotIn(secret, live)
        self.assertEqual(set(rows[0]), {'id', 'time', 'level', 'logger', 'message'})
        self.assertNotIn('\x1b', live)
        self.assertNotIn('\n', live)
        self.assertNotIn('\r', live)

    def test_exception_locations_are_kept_without_values_or_source_text(self):
        try:
            raise RuntimeError('private-ifc-attribute password=private-exception')
        except RuntimeError:
            import sys
            record = logging.LogRecord('application', logging.ERROR, '', 1, 'Operation failed', (), sys.exc_info())
        message = json.loads(SafeJSONFormatter().format(record))['message']
        self.assertIn('RuntimeError', message)
        self.assertIn('test_logging.py:', message)
        self.assertNotIn('private-ifc-attribute', message)
        self.assertNotIn('private-exception', message)
        self.assertNotIn('raise RuntimeError', message)

    def test_django_csrf_diagnostics_do_not_bypass_raw_path_policy(self):
        record = logging.LogRecord('django.security.csrf', logging.WARNING, '', 1,
                                   'Forbidden (%s): %s', ('private-origin', '/models/private-owner/private-file'), None)
        record.status_code = 403
        exported = json.loads(SafeJSONFormatter().format(record))
        self.assertEqual(exported['status_code'], 403)
        self.assertNotIn('private-', json.dumps(exported))

    def test_messages_are_bounded_and_diagnostic_storage_is_optional(self):
        record = logging.LogRecord('application', logging.INFO, '', 1, 'x' * 20000, (), None)
        self.assertLessEqual(len(SafeTextFormatter().format(record)), MAX_MESSAGE_LENGTH)
        with override_settings(ADMIN_LOG_ENABLED=False), patch('apps.shared.live_logs.connection') as connection:
            SharedLogHandler().emit(record)
            install_handler()
        connection.assert_not_called()

    def test_private_keys_escaped_credentials_and_unknown_cookies_are_redacted(self):
        record = logging.LogRecord('application', logging.WARNING, '', 1,
            'password="first\\"private-tail"\n'
            'Cookie: theme=private-theme; unknown=private-cookie\n'
            '-----BEGIN PRIVATE KEY-----\nprivate-key-material\n-----END PRIVATE KEY-----\n'
            'eyJprivateheader.privatepayload.privatesignature', (), None)
        exported = SafeJSONFormatter().format(record)
        for value in ('private-tail', 'private-theme', 'private-cookie', 'private-key-material',
                      'privateheader', 'privatepayload', 'privatesignature'):
            self.assertNotIn(value, exported)
        record.msg = '-----BEGIN PRIVATE KEY-----\n' + 'private-key-material' * 2000 + '\n-----END PRIVATE KEY-----'
        self.assertNotIn('private-key-material', SafeJSONFormatter().format(record))

    @override_settings(ROOT_URLCONF='config.urls')
    def test_runbolt_discovers_runtime_apis_with_logging_configured(self):
        from django_bolt.management.commands.runbolt import Command, find_bolt_api_names
        modules = {'config.api': ['api'], 'apps.mycelium.api': ['api'],
                   'apps.plugin_manager.api': ['api'], 'apps.shared.admin_logs': ['api'],
                   'apps.plugins.browser_pages': ['api'], 'apps.shared.security_metadata': ['api'],
                   'apps.plugins.bim_model_manager.api': ['api', 'passport_api']}
        command = Command()
        for module, names in modules.items():
            self.assertEqual(find_bolt_api_names(module), names)
            for name in names:
                api = command.import_api(f'{module}:{name}', required=True)
                self.assertIs(api._middleware[0], RequestLoggingMiddleware)
        self.assertIn('config.api:api', [name for name, _ in command.autodiscover_apis()])

    def test_existing_store_rows_are_redacted_when_read(self):
        from .live_logs import connection
        from contextlib import closing
        with tempfile.TemporaryDirectory() as directory, override_settings(
                ADMIN_LOG_PATH=Path(directory) / 'logs.sqlite3'):
            with closing(connection()) as db, db:
                db.execute('INSERT INTO logs(time,level,logger,message) VALUES (?,?,?,?)',
                           ('2026-10-04', 'INFO', 'token=private-logger', 'password=private-legacy'))
            exported = json.dumps(read_logs())
        self.assertNotIn('private-', exported)

    def test_install_is_idempotent_and_preserves_configured_level(self):
        root = logging.getLogger()
        old_handlers, old_level = root.handlers[:], root.level
        self.addCleanup(lambda: setattr(root, 'handlers', old_handlers))
        self.addCleanup(root.setLevel, old_level)
        root.handlers = []
        root.setLevel(logging.WARNING)
        with override_settings(ADMIN_LOG_ENABLED=True, LOGGING={'version': 1}):
            install_handler()
            install_handler()
        self.assertEqual(sum(isinstance(handler, SharedLogHandler) for handler in root.handlers), 1)
        self.assertEqual(root.level, logging.WARNING)


class NativeRequestLoggingTests(SimpleTestCase):
    def setUp(self):
        self.capture = JSONCapture()
        self.logger = logging.getLogger('cadevil.requests')
        old_level = self.logger.level
        self.logger.setLevel(logging.INFO)
        self.logger.addHandler(self.capture)
        self.addCleanup(self.logger.removeHandler, self.capture)
        self.addCleanup(self.logger.setLevel, old_level)
        self.api = BoltAPI(django_middleware=True, trailing_slash='keep', enable_logging=False)
        configure_api_logging(self.api)

        @self.api.get('/models/{pk}/assets/{asset_path:path}', guards=[AllowAny()])
        @page_endpoint
        def example(request: Request):
            logging.getLogger('cadevil.requests').info('Handler work', extra={'event': 'handler_work'})
            return JsonResponse({'request_id': request.request_id})

        @self.api.get('/parallel/{pk}', guards=[AllowAny()])
        async def parallel(request: Request):
            logging.getLogger('cadevil.requests').info('Handler work', extra={'event': 'handler_work'})
            await asyncio.sleep(.001)
            return {'request_id': request.META['CADEVIL_REQUEST_ID']}

        @self.api.get('/denied/', guards=[AllowAny()])
        async def denied(request: Request):
            raise HTTPException(403, 'Access denied')

        @self.api.get('/failed/', guards=[AllowAny()])
        async def failed(request: Request):
            raise RuntimeError('private-failure-content')

        @self.api.get('/missing/', guards=[AllowAny()])
        async def missing(request: Request):
            raise FileNotFoundError('private-file-name')

    def test_native_success_and_mount_use_one_validated_id_and_no_payloads(self):
        combined = BoltAPI(enable_logging=False)
        configure_api_logging(combined)
        combined.mount('/mounted', self.api)
        supplied = str(uuid4())
        with TestClient(combined, base_url='http://testserver') as client:
            response = client.get('/mounted/models/private-owner/assets/private-upload.ifc?token=private-query',
                                  headers={'X-Request-ID': supplied, 'Authorization': 'Bearer private-auth',
                                           'Cookie': 'sessionid=private-cookie'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['X-Request-ID'], supplied)
        self.assertEqual(response.json()['request_id'], supplied)
        self.assertTrue(all(entry['request_id'] == supplied for entry in self.capture.entries))
        access = [entry for entry in self.capture.entries if entry.get('event') == 'http_request']
        self.assertEqual(len(access), 1)
        self.assertEqual(access[0]['route'], 'apps.shared.test_logging.example')
        self.assertEqual(access[0]['status_code'], 200)
        self.assertGreaterEqual(access[0]['duration_ms'], 0)
        exported = json.dumps(self.capture.entries)
        for value in ('private-owner', 'private-upload', 'private-query', 'private-auth', 'private-cookie'):
            self.assertNotIn(value, exported)
        self.assertIsNone(request_id_context.get())

    def test_native_errors_and_bad_correlation_header_do_not_log_values(self):
        native_logger = logging.getLogger('django_bolt.error_handlers')
        native_logger.addHandler(self.capture)
        self.addCleanup(native_logger.removeHandler, self.capture)
        with TestClient(self.api, base_url='http://testserver', raise_server_exceptions=False) as client:
            denied = client.get('/denied/', headers={'X-Request-ID': 'private-invalid-id'})
            failed = client.get('/failed/')
            missing = client.get('/missing/')
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(str(UUID(denied.headers['X-Request-ID'])), denied.headers['X-Request-ID'])
        self.assertEqual(failed.status_code, 500)
        self.assertEqual(missing.status_code, 404)
        access = [entry for entry in self.capture.entries if entry.get('event') == 'http_request']
        self.assertEqual([entry['status_code'] for entry in access], [403, 500, 404])
        self.assertNotEqual(access[0]['request_id'], access[1]['request_id'])
        self.assertIn('RuntimeError', access[1]['message'])
        self.assertNotIn('private-failure-content', json.dumps(self.capture.entries))
        self.assertNotIn('private-invalid-id', json.dumps(self.capture.entries))
        self.assertNotIn('private-file-name', json.dumps(self.capture.entries))
        self.assertIsNone(request_id_context.get())

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_early_django_security_redirect_is_correlated_once(self):
        api = BoltAPI(django_middleware=['django.middleware.security.SecurityMiddleware'])
        configure_api_logging(api)
        @api.get('/secure', guards=[AllowAny()])
        async def secure(request: Request):
            raise AssertionError('The security redirect should precede the handler')
        with TestClient(api, base_url='http://testserver') as client:
            response = client.get('/secure', follow_redirects=False)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(str(UUID(response.headers['X-Request-ID'])), response.headers['X-Request-ID'])
        access = [entry for entry in self.capture.entries if entry.get('event') == 'http_request']
        self.assertEqual(len(access), 1)
        self.assertEqual(access[0]['status_code'], 301)

    def test_concurrent_native_requests_keep_independent_context(self):
        ids = [str(uuid4()), str(uuid4())]
        entered = []
        both_entered = None

        @self.api.get('/concurrent/{pk}', guards=[AllowAny()])
        async def concurrent(request: Request):
            nonlocal both_entered
            if both_entered is None:
                both_entered = asyncio.Event()
            entered.append(request.state['request_id'])
            if len(entered) == 2:
                both_entered.set()
            # Both handlers must progress before either returns. A Django sync
            # compatibility bridge held over the await would time out here.
            await asyncio.wait_for(both_entered.wait(), timeout=2)
            return {'request_id': request.state['request_id']}

        async def run():
            async with AsyncTestClient(self.api, base_url='http://testserver') as client:
                return await asyncio.gather(*[
                    client.get(f'/concurrent/{index}', headers={'X-Request-ID': identifier})
                    for index, identifier in enumerate(ids)
                ])
        responses = asyncio.run(run())
        self.assertEqual([response.status_code for response in responses], [200, 200])
        self.assertEqual([response.json()['request_id'] for response in responses], ids)
        self.assertEqual(sorted(entry['request_id'] for entry in self.capture.entries
                                if entry.get('event') == 'http_request'), sorted(ids))
        self.assertIsNone(request_id_context.get())


class AssessmentLoggingTests(SimpleTestCase):
    def test_failure_keeps_context_without_uploaded_file_or_config_data(self):
        from .ifc_extractor.ifc_assessment import assess_ifc
        capture = JSONCapture()
        logger = logging.getLogger('cadevil.assessment')
        old_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(capture)
        self.addCleanup(logger.removeHandler, capture)
        self.addCleanup(logger.setLevel, old_level)
        with patch('apps.shared.ifc_extractor.ifc_assessment._assess_ifc',
                   side_effect=ValueError('private-model-material')):
            with self.assertRaises(ValueError):
                assess_ifc('/tmp/private-owner.ifc', {'private-material': {'token': 'private-config'}},
                           parallel_validation=False)
        self.assertEqual([entry['event'] for entry in capture.entries], ['assessment_started', 'assessment_failed'])
        self.assertEqual(capture.entries[-1]['error_type'], 'ValueError')
        self.assertNotIn('private-', json.dumps(capture.entries))
