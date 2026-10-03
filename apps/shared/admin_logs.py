"""Native Bolt staff page and session-authenticated WebSocket stream."""
import asyncio
from contextlib import suppress
from importlib import import_module
from urllib.parse import urlsplit
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django_bolt import AllowAny, BoltAPI
from django_bolt.websocket import WebSocket, WebSocketDisconnect
from .bolt_pages import page_endpoint
from .page_views import render_page
from .live_logs import read_logs

api = BoltAPI(django_middleware=True, trailing_slash='keep')

@api.get('/admin/logs/', guards=[AllowAny()])
@page_endpoint
@login_required(login_url='/mycelium/login')
@permission_required('shared.view_application_logs', raise_exception=True)
def log_view(request):
    if not request.user.is_active or not request.user.is_staff:
        raise PermissionDenied('Staff access required.')
    response = render_page(request, 'shared/admin_logs.html', {'title': 'Live application logs'})
    response['Cache-Control'] = 'no-store'
    return response

def staff_session(cookies):
    request = HttpRequest()
    SessionStore = import_module(settings.SESSION_ENGINE).SessionStore
    request.session = SessionStore(session_key=cookies.get(settings.SESSION_COOKIE_NAME))
    user = get_user(request)
    return bool(user.is_authenticated and user.is_active and user.is_staff and user.has_perm('shared.view_application_logs'))

def same_origin(headers):
    try:
        origin = urlsplit(headers.get('origin', ''))
        request = HttpRequest()
        request.META['HTTP_HOST'] = headers.get('host', '')
        host = request.get_host()
        return origin.scheme in ('http', 'https') and origin.netloc.lower() == host.lower() and not origin.username and not origin.password
    except Exception:
        return False

@api.websocket('/ws/logs', guards=[AllowAny()], auth=[])
async def log_stream(websocket: WebSocket):
    if not same_origin(websocket.headers) or not await sync_to_async(staff_session)(websocket.cookies):
        await websocket.close(code=4403)
        return
    await websocket.accept()
    received = asyncio.create_task(websocket.receive())
    cursor = None
    try:
        while True:
            if not await sync_to_async(staff_session)(websocket.cookies):
                await websocket.close(code=4403)
                break
            rows = await sync_to_async(read_logs)(cursor)
            if rows:
                await websocket.send_json({'type': 'logs', 'entries': rows})
                cursor = rows[-1]['id']
            elif cursor is None:
                cursor = 0
                await websocket.send_json({'type': 'logs', 'entries': []})
            done, _ = await asyncio.wait({received}, timeout=.5)
            if done:
                received.result()  # Raises WebSocketDisconnect immediately on navigation.
                received = asyncio.create_task(websocket.receive())
    except WebSocketDisconnect:
        pass
    finally:
        received.cancel()
        with suppress(asyncio.CancelledError, WebSocketDisconnect):
            await received
