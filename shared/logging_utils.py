"""Bounded diagnostic logs with an explicit context allowlist.

Never pass request bodies, headers, configuration or IFC attributes to a logger.
Redaction here is a second boundary for third-party and accidental messages.
"""
import json
import logging
import math
import re
import traceback
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

request_id_context = ContextVar('cadevil_request_id', default=None)
MAX_MESSAGE_LENGTH = 8000
_CONTEXT_FIELDS = ('event', 'method', 'route', 'status_code', 'duration_ms',
                   'operation', 'outcome', 'error_type', 'complete', 'issue_count',
                   'material_count', 'loaded_count', 'failed_count')
_SECRET = re.compile(
    r'''(?ix)(["']?(?:password|passwd|pwd|secret(?:_key)?|api[_-]?key|access[_-]?token|refresh[_-]?token|token|authorization|cookie|set-cookie|sessionid|csrftoken|signature|private[_-]?key)["']?\s*[:=]\s*)(?:"(?:\\.|[^"\\\r\n])*"|'(?:\\.|[^'\\\r\n])*'|[^\s,;}&]+)'''
)
_HEADER = re.compile(r'(?im)\b(Authorization|(?:Set-)?Cookie)\s*:\s*[^\r\n]+')
_PRIVATE_KEY = re.compile(r'-----BEGIN ([A-Z ]*PRIVATE KEY)-----.*?(?:-----END \1-----|$)', re.DOTALL)
_JWT = re.compile(r'(?<![\w-])eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?![\w-])')
_AUTH = re.compile(r'(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+')
_URL = re.compile(r'''(?i)\b(?:https?|postgres(?:ql)?|redis)://[^\s<>"']+''')
_PATH = re.compile(r'''(?:/(?:Users|home|private|tmp|var)/|[A-Za-z]:\\|(?:data/)?user_uploads/)[^\s"'<>]+''')
_QUERY = re.compile(r'''(/[^\s?"'<>]*)\?[^\s"'<>]+''')
_CONTROLS = re.compile(r'[\x00-\x08\x0b-\x1f\x7f-\x9f]')


def redact_message(value):
    """Remove credential patterns, URL queries, upload paths and log controls."""
    # Bound work before regex passes as well as bounding the final output.
    message = str(value)[:MAX_MESSAGE_LENGTH * 2]
    message = _PRIVATE_KEY.sub('[PRIVATE KEY REDACTED]', message)
    message = _HEADER.sub(r'\1: [REDACTED]', message)
    message = _JWT.sub('[TOKEN REDACTED]', message)
    message = _AUTH.sub(r'\1 [REDACTED]', message)
    message = _SECRET.sub(r'\1[REDACTED]', message)
    def safe_url(match):
        url = match.group(0).split('?', 1)[0].split('#', 1)[0]
        return re.sub(r'(://)[^/]*@', r'\1[REDACTED]@', url)
    message = _URL.sub(safe_url, message)
    message = _QUERY.sub(r'\1?[REDACTED]', message)
    message = _PATH.sub('[PATH REDACTED]', message)
    # Escape line breaks, ANSI/control characters and Unicode line separators.
    message = _CONTROLS.sub('', message).replace('\r', r'\r').replace('\n', r'\n')
    message = message.replace('\u2028', r'\u2028').replace('\u2029', r'\u2029')
    if len(message) > MAX_MESSAGE_LENGTH:
        message = message[:MAX_MESSAGE_LENGTH - 14] + '… [truncated]'
    return message


def diagnostic_exception(exc_info):
    """Keep actionable stack locations without values, source text or locals."""
    kind, _, tb = exc_info
    frames = traceback.extract_tb(tb, limit=-20)
    locations = [f'{Path(frame.filename).name}:{frame.lineno} in {frame.name}' for frame in frames]
    return f'{kind.__name__}: ' + ' -> '.join(locations)


def record_context(record):
    context = {key: getattr(record, key) for key in _CONTEXT_FIELDS if hasattr(record, key)}
    request_id = getattr(record, 'request_id', None) or request_id_context.get()
    if isinstance(request_id, str) and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', request_id):
        context['request_id'] = request_id
    # Unknown extra fields (including user identifiers) never reach output.
    return {key: redact_message(value)[:500] if isinstance(value, str) else value
            for key, value in context.items()
            if (isinstance(value, (str, int, float, bool)) or value is None)
            and not (isinstance(value, float) and not math.isfinite(value))}


def record_message(record):
    if record.exc_info:
        # Frameworks often interpolate str(exception) into the log message as
        # well as the traceback. Discard both sources of untrusted values.
        return redact_message('Exception recorded | ' + diagnostic_exception(record.exc_info))
    if record.name.startswith(('django.request', 'django.security')):
        # Django's 4xx/CSRF diagnostics interpolate raw paths and caller Origin
        # values even without a traceback. The correlated access event carries
        # the status and safe handler label instead.
        return 'Django request rejected' if record.levelno >= logging.WARNING else 'Django request diagnostic'
    try:
        message = redact_message(record.getMessage())
    except Exception:
        message = 'Diagnostic message could not be formatted.'
    return message


class CorrelationFilter(logging.Filter):
    def filter(self, record):
        record.request_id = request_id_context.get()
        return True


class SafeTextFormatter(logging.Formatter):
    """Human-readable messages keep the existing admin stream protocol."""
    def format(self, record):
        message = record_message(record)
        context = record_context(record)
        if context:
            # Keep correlation visible even when a long message is truncated.
            message = json.dumps(context, ensure_ascii=True, separators=(',', ':')) + ' ' + message
        return redact_message(message)


class SafeJSONFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            'time': datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            'level': record.levelname,
            'logger': redact_message(record.name),
            'message': record_message(record),
            'process': record.process,
            **record_context(record),
        }
        return json.dumps(payload, ensure_ascii=True, separators=(',', ':'), allow_nan=False)
