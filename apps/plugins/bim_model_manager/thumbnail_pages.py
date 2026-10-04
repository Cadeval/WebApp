"""Private, lazily generated building previews for owned BIM selections."""
from pathlib import Path
import asyncio
import re

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_http_methods
from django_bolt.exceptions import HTTPException

from apps.shared.bolt_pages import form_request
from apps.plugins.bim_model_manager.django.models import FileUpload
from .exchange_pages import local_source
from .passport_views import _enabled


@login_required(login_url='/mycelium/login')
@require_http_methods(['GET'])
def _resolve_thumbnail(request, pk):
    # Resolve access before opening the source or touching any shared cache.
    _enabled(request)
    upload = get_object_or_404(FileUpload, pk=pk, user=request.user)
    building = request.GET.get('building', '')
    if building and not re.fullmatch(r'[0-3][0-9A-Za-z_$]{21}', building):
        return HttpResponse('Choose a building from this model.', status=400,
                            content_type='text/plain; charset=utf-8')
    return upload, building


def _render_thumbnail(upload, building):
    # This phase has no ORM queries or session work and can leave Django's
    # shared synchronous lane free while native geometry is being prepared.
    from apps.plugins.bim_model_manager.building_thumbnails import model_thumbnail as create_thumbnail, ThumbnailError
    cache = Path(getattr(settings, 'BUILDING_THUMBNAIL_CACHE_ROOT',
                         Path(settings.BASE_DIR) / 'data' / 'building-thumbnail-cache'))
    try:
        with local_source(upload) as source:
            path = create_thumbnail(source, cache, building_guid=building,
                                    geometry_cache_directory=Path(settings.MEDIA_ROOT) / 'bim-viewer-cache')
        content = path.read_bytes()
        if not content or len(content) > 1024 * 1024:
            raise ThumbnailError('Preview exceeds its image size limit.')
        response = HttpResponse(content, content_type='image/png')
    except (ThumbnailError, OSError, ValueError, RuntimeError):
        # A missing preview must not prevent selecting or opening the model.
        return HttpResponse('Preview unavailable. You can still select and open this model.',
                            status=422, content_type='text/plain; charset=utf-8')
    response['Content-Length'] = str(len(content))
    response['Content-Disposition'] = 'inline; filename="building-preview.png"'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


async def model_thumbnail(request, pk):
    """Authorize in Django's lane, then prepare the PNG in a worker thread."""
    adapted = await sync_to_async(form_request, thread_sensitive=True)(request)
    try:
        resolved = await sync_to_async(_resolve_thumbnail, thread_sensitive=True)(adapted, pk)
        response = resolved if isinstance(resolved, HttpResponse) else await asyncio.to_thread(_render_thumbnail, *resolved)
        response['Cache-Control'] = 'private, no-store'
        patch_vary_headers(response, ['Cookie'])
        return response
    except Http404 as error:
        raise HTTPException(404, str(error)) from error
    except PermissionDenied as error:
        raise HTTPException(403, str(error)) from error
    except SuspiciousOperation as error:
        raise HTTPException(400, str(error)) from error
    finally:
        await sync_to_async(adapted.close, thread_sensitive=True)()
