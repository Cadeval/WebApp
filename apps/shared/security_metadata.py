"""Public, fixed-source disclosure metadata; never serves uploaded files."""
from datetime import datetime
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import HttpResponse, HttpResponsePermanentRedirect
from django_bolt import AllowAny, BoltAPI

from .bolt_pages import page_endpoint
from .page_views import render_page
from .request_logging import configure_api_logging

PROJECT_ROOT = Path(__file__).resolve().parents[2]
api = BoltAPI(trailing_slash='keep', django_middleware=True)
configure_api_logging(api)


def _uri(value, schemes):
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError('Invalid disclosure URI.')
    decoded = unquote(value)
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in decoded):
        raise ValueError('Disclosure URIs cannot contain whitespace or controls.')
    parts = urlsplit(value)
    if parts.scheme not in schemes or parts.username or parts.password or parts.fragment:
        raise ValueError('Disclosure URI must use an approved scheme without credentials or fragments.')
    if parts.scheme == 'mailto':
        if parts.netloc or parts.query:
            raise ValueError('Disclosure email must contain one address.')
        try:
            validate_email(unquote(parts.path))
        except ValidationError as error:
            raise ValueError('Invalid disclosure email.') from error
    elif parts.scheme == 'https':
        if not parts.hostname:
            raise ValueError('Disclosure web URI must use HTTPS and a hostname.')
        try:
            parts.port
        except ValueError as error:
            raise ValueError('Invalid disclosure URI port.') from error
    return value


def disclosure_fields():
    source = (PROJECT_ROOT/'.well-known/security.txt').read_text(encoding='utf-8')
    defaults = dict(line.split(': ', 1) for line in source.splitlines() if line and not line.startswith('#'))
    contact = _uri(getattr(settings, 'SECURITY_TXT_CONTACT', '') or defaults['Contact'], {'mailto', 'https'})
    expires = getattr(settings, 'SECURITY_TXT_EXPIRES', '') or defaults['Expires']
    if not isinstance(expires, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', expires):
        raise ValueError('Disclosure expiry must be an RFC 3339 UTC timestamp.')
    try:
        expiry = datetime.fromisoformat(expires.replace('Z', '+00:00'))
    except (ValueError, AttributeError, TypeError) as error:
        raise ValueError('Disclosure expiry must be an RFC 3339 UTC timestamp.') from error
    if expiry.tzinfo is None:
        raise ValueError('Disclosure expiry must be an RFC 3339 UTC timestamp.')
    fields = {'Contact': contact, 'Expires': expires, 'Preferred-Languages': defaults['Preferred-Languages']}
    for name in ('Canonical', 'Policy'):
        value = getattr(settings, 'SECURITY_TXT_'+name.upper(), '')
        if value:
            fields[name] = _uri(value, {'https'})
    return fields


def _public_file(content, content_type):
    response = HttpResponse(content, content_type=content_type)
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'public, max-age=3600'
    return response


@api.get('/.well-known/security.txt', name='security_txt', guards=[AllowAny()])
@page_endpoint
def security_txt(request):
    try:
        fields = disclosure_fields()
    except (ValueError, OSError, KeyError):
        return HttpResponse('Security contact metadata is misconfigured. Please contact the site operator.\n',
                            status=503, content_type='text/plain; charset=utf-8')
    return _public_file(''.join(f'{name}: {value}\n' for name, value in fields.items()),
                        'text/plain; charset=utf-8')


@api.get('/security.txt', guards=[AllowAny()])
@api.get('/.well_known/security.txt', guards=[AllowAny()])
@page_endpoint
def legacy_security_txt(request):
    return HttpResponsePermanentRedirect('/.well-known/security.txt')


@api.get('/security', name='security_policy', guards=[AllowAny()])
@page_endpoint
def security_policy(request):
    try:
        contact = disclosure_fields()['Contact']
    except (ValueError, OSError, KeyError):
        contact = None
    return render_page(request, 'shared/security_policy.html',
                       {'title': 'Security', 'security_contact': contact})


@api.get('/accessibility', name='accessibility_statement', guards=[AllowAny()])
@page_endpoint
def accessibility_statement(request):
    return render_page(request, 'shared/accessibility_statement.html', {'title': 'Accessibility'})


@api.get('/security/sbom.json', name='audit_sbom', guards=[AllowAny()])
@page_endpoint
def audit_sbom(request):
    source = PROJECT_ROOT/'sbom/cadevil.cdx.json'
    if not source.is_file():
        return HttpResponse('The release SBOM is unavailable.\n', status=503, content_type='text/plain; charset=utf-8')
    response = _public_file(source.read_bytes(), 'application/vnd.cyclonedx+json')
    response['Content-Disposition'] = 'attachment; filename="cadevil.cdx.json"'
    return response
