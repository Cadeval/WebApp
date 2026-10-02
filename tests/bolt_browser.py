"""Browser-style tests over Bolt's real Rust HTTP test transport."""
from urllib.parse import urlencode

from django.http import HttpResponse, HttpResponseRedirect
from django.test import Client
from django.test.client import BOUNDARY, MULTIPART_CONTENT, encode_multipart
from django.test.signals import template_rendered
from django_bolt.testing import TestClient

from config.api import api


class BoltBrowser:
    extra = {}
    headers = {}

    def __init__(self, *, csrf=True, api=api):
        self.transport = TestClient(api, base_url='http://testserver')
        self.transport.__enter__()
        self.auto_csrf = csrf

    def close(self):
        self.transport.__exit__(None, None, None)

    def force_login(self, user):
        session = Client()
        session.force_login(user)
        self.transport.cookies.set('sessionid', session.cookies['sessionid'].value)

    def logout(self):
        self.transport.cookies.clear()

    def get(self, path, data=None, **kwargs):
        if data:
            path += ('&' if '?' in path else '?') + urlencode(data, doseq=True)
        return self.request('GET', path, **kwargs)

    def post(self, path, data=None, **kwargs):
        data = data or {}
        if any(hasattr(value, 'read') for value in data.values()):
            body = encode_multipart(BOUNDARY, data)
            content_type = MULTIPART_CONTENT
        else:
            body = urlencode(data, doseq=True).encode()
            content_type = 'application/x-www-form-urlencoded'
        return self.request('POST', path, content=body, content_type=content_type, **kwargs)

    def request(self, method, path, content=None, content_type=None, **kwargs):
        headers = dict(kwargs.pop('headers', {}))
        for key, value in kwargs.items():
            if key.startswith('HTTP_'):
                headers[key[5:].replace('_', '-')] = value
        if content_type:
            headers['Content-Type'] = content_type
        if method == 'POST' and self.auto_csrf:
            if 'csrftoken' not in self.transport.cookies:
                self.get('/plugins/manage/')
                if 'csrftoken' not in self.transport.cookies:
                    self.get('/plugins/bim/model_manager/')
            headers['X-CSRFToken'] = self.transport.cookies.get('csrftoken', '')
        contexts = []
        def capture(sender, template, context, **_):
            contexts.append(context.flatten())
        template_rendered.connect(capture, weak=False)
        try:
            result = self.transport.request(method, path, content=content, headers=headers, follow_redirects=False)
        finally:
            template_rendered.disconnect(capture)
        if result.status_code in (301, 302, 303, 307, 308):
            response = HttpResponseRedirect(result.headers['location'])
            response.status_code = result.status_code
        else:
            response = HttpResponse(result.content, status=result.status_code)
        for key, value in result.headers.items():
            response[key] = value
        response.context = contexts[0] if contexts else None
        response.json = result.json
        response.client = self
        return response
