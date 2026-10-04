"""Async request correlation outside Bolt's Django middleware adapter."""
import logging
import time
from uuid import UUID, uuid4

from django.core.exceptions import PermissionDenied
from django_bolt import BoltAPI
from django_bolt.exceptions import HTTPException, ResponseValidationError, ValidationException
from msgspec import ValidationError

from .logging_utils import request_id_context

logger = logging.getLogger('cadevil.requests')


def error_status(error):
    # Match Bolt's built-in error response mapping without serializing values.
    if isinstance(error, HTTPException):
        return error.status_code
    if isinstance(error, FileNotFoundError):
        return 404
    if isinstance(error, (PermissionError, PermissionDenied)):
        return 403
    if not isinstance(error, ResponseValidationError) and isinstance(error, (ValidationException, ValidationError)):
        return 422
    return 500


def correlation_id(value):
    # Only canonical UUIDs are accepted from callers, never arbitrary log text.
    try:
        parsed = UUID(value)
        if str(parsed) == value.lower():
            return str(parsed)
    except (ValueError, AttributeError, TypeError):
        pass
    return str(uuid4())


class RequestLoggingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def _finish(self, request, started, status, error=None):
        extra = {'event': 'http_request', 'method': request.method,
                 'route': request.META.get('CADEVIL_LOG_ROUTE', '<native>'), 'status_code': status,
                 'duration_ms': round((time.perf_counter() - started) * 1000, 2)}
        if error is not None:
            extra['error_type'] = type(error).__name__
        level = logging.ERROR if status >= 500 else logging.WARNING if status >= 400 else logging.INFO
        logger.log(level, 'HTTP request completed', extra=extra,
                   exc_info=bool(error is not None and status >= 500))

    async def __call__(self, request):
        identifier = correlation_id(request.headers.get('x-request-id'))
        request.state['request_id'] = identifier
        # META is shared with native and adapted Django form requests.
        request.META['CADEVIL_REQUEST_ID'] = identifier
        token = request_id_context.set(identifier)
        started = time.perf_counter()
        try:
            response = await self.get_response(request)
        except Exception as error:
            self._finish(request, started, error_status(error), error)
            if isinstance(error, HTTPException):
                error.headers['X-Request-ID'] = identifier
            raise
        else:
            response.headers['X-Request-ID'] = identifier
            self._finish(request, started, response.status_code)
            return response
        finally:
            request_id_context.reset(token)


def configure_api_logging(api: BoltAPI) -> BoltAPI:
    """Compose logging before the native Django stack on every owning API.

    Bolt 0.11.1 appends custom middleware after Django's adapter. Move this
    native async wrapper to the front before route registration or chain
    compilation so early Django rejections are correlated too. Keeping it
    outside settings.MIDDLEWARE avoids the adapter's sync compatibility bridge.
    Mounted APIs preserve the owning API's chain, so each request logs once.
    Keep a literal ``api = BoltAPI(...)`` assignment: runbolt discovers those
    calls statically and does not recognize factories or subclass aliases.
    """
    if api._middleware_chain_built:
        raise RuntimeError('Configure API logging before serving requests.')
    if RequestLoggingMiddleware not in api._middleware:
        api._middleware.insert(0, RequestLoggingMiddleware)
    api._has_python_global_middleware = True
    return api
