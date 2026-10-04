"""Location context for an owned building, using the same effective map marker."""
from pathlib import Path
import re

from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.cache import patch_vary_headers
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from plugins.bim_model_manager.location_lookup import lookup_location, valid_coordinates
from plugins.bim_model_manager.django.models import FileUpload
from .exchange_pages import _source_locations, _locations
from .pages import bim_page, page


@bim_page
@never_cache
@require_http_methods(['GET', 'POST'])
def building_context(request, pk, guid):
    upload = get_object_or_404(FileUpload.objects.select_related('conversion').prefetch_related('building_locations'), pk=pk, user=request.user)
    if not re.fullmatch(r'[A-Za-z0-9_$]{22}', guid):
        raise Http404('Invalid building identifier.')
    source_error = False
    try:
        data = _source_locations(upload)
        building = next((row for row in _locations(upload, data) if row['guid'] == guid), None)
    except (OSError, RuntimeError, ValueError):
        source_error = True
        building = {'guid': guid, 'name': 'Building location unavailable', 'latitude': None, 'longitude': None,
                    'message': 'The IFC location could not be read. Open the model to inspect its source.', 'source': ''}
    if building is None:
        raise Http404('This building is not in your uploaded model.')
    if request.method == 'POST' and request.POST.get('action') != 'refresh':
        raise Http404('This lookup action is not available.')
    cache = Path(getattr(settings, 'LOCATION_LOOKUP_CACHE_ROOT', Path(settings.BASE_DIR) / 'data/location-lookup-cache'))
    latitude, longitude = building.get('latitude'), building.get('longitude')
    if valid_coordinates(latitude, longitude):
        lookup = lookup_location(latitude, longitude, cache, refresh=request.method == 'POST')
    else:
        unavailable = {'status': 'missing', 'message': 'Set a building location on the map to look up local data.'}
        lookup = {'country': {'status': 'missing', 'label': 'Location needed', 'note': unavailable['message']},
                  'energy': dict(unavailable), 'planning': dict(unavailable),
                  'utilities': {'status': 'missing', 'items': [], 'note': unavailable['message']}}
    # HTMX 4 identifies its target as "tag#id"; older clients send the ID.
    target_match = re.fullmatch(r'(?:(?:div|section)#)?(building-map-context-[0-9]+|building-context-results)',
                               request.headers.get('HX-Target', ''))
    target = target_match.group(1) if target_match else ''
    partial = (request.headers.get('HX-Request') == 'true'
              and request.headers.get('HX-Request-Type') != 'full'
              and request.headers.get('HX-History-Restore-Request') != 'true'
              and (target == 'building-context-results' or bool(re.fullmatch(r'building-map-context-[0-9]+', target))))
    inline = partial and target != 'building-context-results'
    context = {'title': 'Local data & building rules', 'document': upload, 'building': building,
               'lookup': lookup, 'inline': inline, 'result_id': target if partial else 'building-context-results',
               'lookup_url': reverse('bim:building_context', args=[upload.pk, guid]),
               'location_url': '' if source_error else reverse('bim:building_location', args=[upload.pk, guid]),
               'viewer_url': reverse('bim:viewer', args=[upload.pk]) + '?from=map',
               'approximate': building.get('source') == 'ifc_site'}
    if partial:
        response = render(request, 'bim/_building_context_results.html', context)
        patch_vary_headers(response, ['HX-Target', 'HX-Request-Type', 'HX-History-Restore-Request'])
        return response
    return page(request, 'bim/building_context.html', context)
