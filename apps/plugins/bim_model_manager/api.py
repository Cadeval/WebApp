"""Direct Bolt routes for the BIM workspace and shared passport handlers."""
from uuid import UUID

from django_bolt import AllowAny, BoltAPI
from django_bolt.exceptions import HTTPException
from django_bolt.request import Request

from apps.shared.bolt_pages import page_endpoint
from . import pages, passport_views

# Session authentication, ownership and the runtime plugin gate live in the
# page handlers. AllowAny prevents unrelated global JWT defaults overriding
# their browser login redirects. Django middleware still supplies sessions.
api = BoltAPI(namespace='bim', trailing_slash='keep', django_middleware=True)
passport_api = BoltAPI(namespace='material_passport', trailing_slash='keep', django_middleware=True)


def object_id(request):
    try:
        return UUID(request.params['pk'])
    except (ValueError, KeyError) as error:
        raise HTTPException(404, 'Invalid object identifier.') from error


@api.get('/plugins/bim/model_manager/', name='model_manager', guards=[AllowAny()])
@api.post('/plugins/bim/model_manager/', name='model_manager', guards=[AllowAny()])
@page_endpoint
def model_manager(request: Request):
    return pages.model_manager(request)


@api.get('/plugins/bim/configuration_library/', name='configuration_library', guards=[AllowAny()])
@api.post('/plugins/bim/configuration_library/', name='configuration_library', guards=[AllowAny()])
@page_endpoint
def configuration_library(request: Request):
    return pages.library(request)


@api.get('/plugins/bim/config_editor/', name='config_editor', guards=[AllowAny()])
@api.post('/plugins/bim/config_editor/', name='config_editor', guards=[AllowAny()])
@page_endpoint
def config_editor(request: Request):
    return pages.config_editor(request)


@api.post('/plugins/bim/save_config/', name='save_config', guards=[AllowAny()])
@page_endpoint
def save_config(request: Request):
    return pages.select_config(request)


@api.get('/plugins/bim/download_csv/', name='download_csv', guards=[AllowAny()])
@page_endpoint
def download_csv(request: Request):
    return pages.download_config(request)


@api.get('/plugins/bim/models/{pk}/download/', name='download_model', guards=[AllowAny()])
@page_endpoint
def download_model(request: Request):
    return pages.download_model(request, object_id(request))


@api.get('/plugins/bim/models/{pk}/viewer/', name='viewer', guards=[AllowAny()])
@page_endpoint
def viewer(request: Request):
    return pages.viewer(request, object_id(request))


@api.get('/plugins/bim/models/{pk}/geometry/', name='model_geometry', guards=[AllowAny()])
@page_endpoint
def model_geometry(request: Request):
    return pages.stream_model(request, object_id(request))


@api.get('/plugins/bim/models/{pk}/materials/', name='viewer_materials', guards=[AllowAny()])
@page_endpoint
def viewer_materials(request: Request):
    return pages.viewer_materials(request, object_id(request))


@api.post('/plugins/bim/models/{pk}/delete/', name='delete_model', guards=[AllowAny()])
@page_endpoint
def delete_model(request: Request):
    return pages.delete_model(request, object_id(request))


@passport_api.get('/plugins/bim/material-passport/', name='calculate', guards=[AllowAny()])
@passport_api.post('/plugins/bim/material-passport/', name='calculate', guards=[AllowAny()])
@page_endpoint
def calculate(request: Request):
    return passport_views.calculate(request)


@passport_api.get('/plugins/bim/material-passport/compare/', name='compare', guards=[AllowAny()])
@passport_api.post('/plugins/bim/material-passport/compare/', name='compare', guards=[AllowAny()])
@page_endpoint
def compare(request: Request):
    return passport_views.compare(request)


@passport_api.get('/plugins/bim/material-passport/{pk}/', name='report', guards=[AllowAny()])
@page_endpoint
def report(request: Request):
    return passport_views.report(request, object_id(request))


# Native API composition copies routes into Bolt's Rust router; no ASGI or
# Django URLconf is mounted. Preserve the two existing reverse namespaces.
api.mount('', passport_api)
