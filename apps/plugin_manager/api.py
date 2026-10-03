from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.vary import vary_on_headers
from django_bolt import BoltAPI, AllowAny
from apps.shared.bolt_pages import page_endpoint
from django_bolt.request import Request

from apps.plugin_manager.models import PluginActivationError, PluginRecord
from apps.plugin_manager.services import (
    manage_plugin,
    reload_plugins,
)
from apps.shared.services import staff_required
from .store import catalog_entry, catalog_response, plugin_catalog, store_upload
from .models import UserPluginSelection


def _row_context(request, record):
    selected = UserPluginSelection.objects.filter(user=request.user, plugin=record).exists()
    return catalog_entry(record, selected)

api = BoltAPI(namespace="plugin_manager",trailing_slash="keep",django_middleware=True)


def _toggle(request: HttpRequest, plugin_id: str, enabled: bool) -> HttpResponse:
    record = get_object_or_404(PluginRecord, plugin_id=plugin_id)
    try:
        manage_plugin(plugin_id, "load" if enabled else "unload")
    except PluginActivationError:
        record.refresh_from_db()
        response = catalog_response(request, manager=True, notice='This plugin is unavailable. Review its status below before enabling it.', status=409)
        response["HX-Push-Url"] = "false"
        return response
    record.refresh_from_db()
    response = catalog_response(request, manager=True, notice='Site availability updated. Personal selections were preserved.')
    response["HX-Push-Url"] = "false"
    return response


@staff_required
def plugin_enable(request: HttpRequest, plugin_id: str) -> HttpResponse:
    return _toggle(request, plugin_id, True)


@staff_required
def plugin_disable(request: HttpRequest, plugin_id: str) -> HttpResponse:
    return _toggle(request, plugin_id, False)


@login_required(login_url='/mycelium/login')
def plugin_upload(request: HttpRequest) -> HttpResponse:
    return store_upload(request)


@staff_required
@vary_on_headers("HX-Request")
def plugin_reload(request: HttpRequest) -> HttpResponse:
    results = reload_plugins()
    if not request.headers.get("HX-Request"):
        return redirect("/plugins/manage/")
    loaded = sum(1 for result in results if result.ok)
    failed = len(results) - loaded
    return catalog_response(request, manager=True, reload_summary=f"Discovery refreshed: {loaded} loaded, {failed} failed. Restart the server after changing installed Python code.")


@api.get('/plugins/manage/',name='plugin_list',guards=[AllowAny()])
@page_endpoint
def manager_page(request: Request):
    return plugin_catalog(request)

@api.post('/plugins/{plugin_id}/enable/',name='plugin_enable',guards=[AllowAny()])
@page_endpoint
def manager_enable(request: Request):
    return plugin_enable(request,request.params['plugin_id'])

@api.post('/plugins/{plugin_id}/disable/',name='plugin_disable',guards=[AllowAny()])
@page_endpoint
def manager_disable(request: Request):
    return plugin_disable(request,request.params['plugin_id'])

@api.post('/plugins/upload/',name='plugin_upload',guards=[AllowAny()])
@page_endpoint
def manager_upload(request: Request):
    return plugin_upload(request)

@api.post('/plugins/reload/',name='plugin_reload',guards=[AllowAny()])
@page_endpoint
def manager_reload(request: Request):
    return plugin_reload(request)



from .store import plugin_store, store_upload, store_action, package_asset, sample_package

@api.get('/plugins/workflows/{plugin_id}/',name='plugin_workflow',guards=[AllowAny()])
@page_endpoint
def selected_workflow(request: Request):
    from .store import plugin_workflow
    return plugin_workflow(request, request.params['plugin_id'])

@api.get('/plugins/store/',name='plugin_store',guards=[AllowAny()])
@page_endpoint
def store_page(request: Request): return plugin_store(request)

@api.post('/plugins/store/upload/',name='plugin_store_upload',guards=[AllowAny()])
@page_endpoint
def upload_store_package(request: Request): return store_upload(request)

@api.post('/plugins/store/{plugin_id}/{action}/',name='plugin_store_action',guards=[AllowAny()])
@page_endpoint
def toggle_store_package(request: Request): return store_action(request,request.params['plugin_id'],request.params['action'])

@api.get('/plugins/{plugin_id}/assets/{asset_path:path}',name='plugin_asset',guards=[AllowAny()])
@page_endpoint
def uploaded_package_asset(request: Request): return package_asset(request,request.params['plugin_id'],request.params['asset_path'])

@api.get('/plugins/store/example.zip',name='plugin_sample',guards=[AllowAny()])
@page_endpoint
def download_package_example(request: Request): return sample_package(request)

from .keys import signing_keys_page, register_signing_key, revoke_signing_key, signing_cli

@api.get('/plugins/keys/',name='plugin_keys',guards=[AllowAny()])
@page_endpoint
def keys_page(request: Request): return signing_keys_page(request)

@api.post('/plugins/keys/register/',name='plugin_key_register',guards=[AllowAny()])
@page_endpoint
def create_public_signing_key(request: Request): return register_signing_key(request)

@api.post('/plugins/keys/{key_id}/revoke/',name='plugin_key_revoke',guards=[AllowAny()])
@page_endpoint
def revoke_public_signing_key(request: Request): return revoke_signing_key(request,request.params['key_id'])

@api.get('/plugins/keys/cli.py',name='plugin_sign_cli',guards=[AllowAny()])
@page_endpoint
def download_signing_cli(request: Request): return signing_cli(request)

from .store import package_download

@api.get('/plugins/{plugin_id}/package.zip',name='plugin_package_download',guards=[AllowAny()])
@page_endpoint
def review_package_download(request: Request): return package_download(request,request.params['plugin_id'])
