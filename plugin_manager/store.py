"""Shared catalog, personal plugin collection and protected package assets."""
import hashlib
import io
import json
from zipfile import ZipFile, BadZipFile
from pathlib import PurePosixPath

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import HttpResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.cache import patch_vary_headers
from django.utils.dateparse import parse_datetime

from shared.page_views import render_page
from shared.services import staff_required
from .forms import PluginUploadForm
from .models import PluginRecord, UserPluginSelection
from .services import create_uploaded_plugin
from .packages import safe_path, MAX_MEMBER_BYTES
from .workflows import is_workflow_plugin, selectable_plugin, workflow_plugin_enabled

BUILTINS={
    'cadevil.bim.model_manager':('BIM Workspace','Manage IFC models, reference configurations, material passports and comparisons.','/plugins/bim/'),
    'cadevil.example.editor':('Rust IFC Editor','Inspect and edit a local IFC file in a Rust/WebAssembly worker.','/plugins/ifc-editor/'),
    'cadevil.rust-example.editor':('Rust Snake','Play the bundled game powered by a Rust/WebAssembly worker.','/plugins/rust-snake/'),
}


def catalog_entry(record, selected=False):
    builtin = BUILTINS.get(record.plugin_id)
    description = (builtin[1] if builtin else record.package_manifest.get('description', '')) or (
        'Installed Python package. Server installation and code updates require a restart.'
        if record.source == 'package' else 'Reviewed browser plugin running in a background worker.')
    icon = {'cadevil.bim.model_manager': 'fa-building-o', 'cadevil.example.editor': 'fa-cube',
            'cadevil.rust-example.editor': 'fa-gamepad'}.get(record.plugin_id, 'fa-puzzle-piece')
    return {'record': record, 'description': description, 'icon': icon,
            'url': builtin[2] if builtin else (reverse('plugin_manager:plugin_workflow', args=[record.plugin_id]) if is_workflow_plugin(record) else ''),
            'category': 'Bundled tool' if builtin else ('Installed Python package' if record.source == 'package' else 'Uploaded browser plugin'),
            'selected': selected, 'available': selectable_plugin(record), 'is_workflow': is_workflow_plugin(record)}


def catalog_response(request, *, manager=True, form=None, notice='', reload_summary='', status=200):
    query=request.GET.get('q','').strip()[:200]
    active_tab = request.POST.get('tab') or request.GET.get('tab', 'workflows')
    if form is not None or request.path.endswith('/upload/'):
        active_tab = 'developer'
    elif reload_summary:
        active_tab = 'management'
    if active_tab not in {'workflows', 'management', 'developer'}:
        active_tab = 'workflows'
    selected = set(UserPluginSelection.objects.filter(user=request.user).values_list('plugin_id', flat=True))
    catalog=[]
    selected_catalog=[]
    available_catalog=[]
    records = PluginRecord.objects.select_related('signing_key__owner', 'uploaded_by')
    for record in records:
        item = catalog_entry(record, record.pk in selected)
        if not request.user.is_staff and not item['available'] and not (item['selected'] and item['is_workflow']): continue
        if query and query.casefold() not in (' '.join([record.plugin_id, record.name, item['description']])).casefold(): continue
        catalog.append(item)
        if item['is_workflow'] and item['selected']: selected_catalog.append(item)
        elif item['is_workflow'] and item['available']: available_catalog.append(item)
    response=render_page(request,'plugin_manager/plugins.jinja2',
                         {'catalog':catalog, 'records':[item['record'] for item in catalog], 'query':query,
                          'selected_catalog':selected_catalog, 'available_catalog':available_catalog,
                          'active_tab':active_tab,
                          'notice':notice, 'reload_summary':reload_summary, 'upload_form':form or PluginUploadForm()}, status=status)
    if request.method=='POST': response['HX-Push-Url']='false'
    return response


def plugin_detail_metadata(record):
    """Honest artifact metadata, without inspecting arbitrary installed files."""
    size = None
    if record.source == PluginRecord.Source.UPLOAD and record.artifact:
        try:
            size = record.artifact.size
        except (OSError, ValueError):
            pass
    history = []
    for entry in reversed(record.version_history or []):
        if not isinstance(entry, dict) or not isinstance(entry.get('version'), str):
            continue
        try:
            observed_date = parse_datetime(entry.get('observed_at', '')) if isinstance(entry.get('observed_at'), str) else None
        except ValueError:
            observed_date = None
        history.append({**entry, 'observed_date': observed_date})
    return {'size_bytes': size, 'uploader': (record.uploaded_by.get_username() if record.uploaded_by
            else 'Former account') if record.source == PluginRecord.Source.UPLOAD else 'Site installation',
            'history': history}


@login_required(login_url='/mycelium/login')
def plugin_details(request, plugin_id):
    from .django_resources import get_overview_for_plugin

    record = get_object_or_404(PluginRecord.objects.select_related('uploaded_by', 'signing_key__owner'),
                               plugin_id=plugin_id)
    selected = UserPluginSelection.objects.filter(user=request.user, plugin=record).exists()
    item = catalog_entry(record, selected)
    if not request.user.is_staff and not item['available'] and not (selected and item['is_workflow']):
        raise Http404('Plugin is unavailable.')
    context = {'item': item, 'record': record, **plugin_detail_metadata(record),
               'overview_template': get_overview_for_plugin(plugin_id)}
    drawer = (request.headers.get('HX-Request') == 'true'
              and request.headers.get('HX-Target') in {'plugin-detail-body', '#plugin-detail-body', 'div#plugin-detail-body'}
              and request.headers.get('HX-History-Restore-Request') != 'true'
              and request.headers.get('HX-Request-Type') != 'full')
    if drawer:
        response = HttpResponse(render_to_string('plugin_manager/_plugin_detail.html', context, request=request))
    else:
        response = render_page(request, 'plugin_manager/plugin_detail.html', context)
    patch_vary_headers(response, ['HX-Request', 'HX-Target', 'HX-History-Restore-Request', 'HX-Request-Type'])
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required(login_url='/mycelium/login')
def plugin_catalog(request):
    return catalog_response(request, manager=True)


@login_required(login_url='/mycelium/login')
def plugin_store(request):
    if request.headers.get('HX-Request') == 'true':
        response = catalog_response(request)
        response['HX-Replace-Url'] = reverse('plugin_manager:plugin_list')
        return response
    return redirect('plugin_manager:plugin_list')


@login_required(login_url='/mycelium/login')
def store_upload(request):
    form=PluginUploadForm(request.POST,request.FILES)
    if not form.is_valid(): return catalog_response(request,manager=True,form=form,status=400)
    artifact=form.cleaned_data["artifact"]
    from .teams import can_publish_key
    if not can_publish_key(request.user,artifact.signing_key):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("Publish a package signed with your own key or a current team's key.")
    try: record=create_uploaded_plugin(form,request.user)
    except IntegrityError:
        form.add_error('artifact','A plugin with this id was installed while the upload was being processed. Choose a different id.')
        return catalog_response(request,manager=True,form=form,status=409)
    if request.headers.get('HX-Request')!='true': return redirect(reverse('plugin_manager:plugin_list') + '?tab=developer')
    return catalog_response(request,manager=True,notice=f'{record.name} uploaded. ' + ('Review its code and enable it for the site when ready.' if request.user.is_staff else 'An administrator must review and enable it.'),status=201)


@login_required(login_url='/mycelium/login')
def store_action(request,plugin_id,action):
    if action not in {'enable','disable'}: raise Http404('Unknown plugin store action.')
    record = get_object_or_404(PluginRecord.objects.select_related('signing_key'),plugin_id=plugin_id)
    manager = True
    if action == 'enable':
        if not selectable_plugin(record):
            return catalog_response(request,manager=manager,notice='This plugin is unavailable for personal workflows. An administrator must approve it for this environment.',status=409)
        UserPluginSelection.objects.get_or_create(user=request.user,plugin=record)
    else:
        UserPluginSelection.objects.filter(user=request.user,plugin=record).delete()
    if request.headers.get('HX-Request')!='true':
        return redirect('plugin_manager:plugin_list')
    return catalog_response(request,manager=manager,notice='Plugin enabled for your workflow.' if action=='enable' else 'Plugin removed from your workflow.')


@login_required(login_url='/mycelium/login')
def plugin_workflow(request, plugin_id):
    if not workflow_plugin_enabled(request.user, plugin_id):
        raise Http404('This plugin is not enabled for your workflow.')
    from .context_processors import plugin_editor_items
    items = [item for item in plugin_editor_items(request)['plugin_editor_items'] if item.id == plugin_id]
    if not items:
        raise Http404('This plugin does not provide a browser worker.')
    record = get_object_or_404(PluginRecord, plugin_id=plugin_id)
    return render_page(request, 'plugin_manager/workflow.html', {'record':record, 'plugin_editor_items':items})


@login_required(login_url='/mycelium/login')
def package_asset(request,plugin_id,asset_path):
    if not workflow_plugin_enabled(request.user, plugin_id):
        raise Http404('This plugin is not enabled for your workflow.')
    record=get_object_or_404(PluginRecord,plugin_id=plugin_id,source='upload',artifact_type='zip',enabled=True,error='',signing_key__isnull=False,signing_key__revoked_at__isnull=True,signing_key__owner__isnull=False)
    if not record.environment_compatible: raise Http404('Plugin is unavailable in this environment.')
    from .certificate_authority import trusted_key
    if not trusted_key(record.signing_key): raise Http404('Plugin signing trust is unavailable.')
    try: safe_path(asset_path)
    except ValidationError as error: raise Http404('Package asset is unavailable.') from error
    suffix=PurePosixPath(asset_path).suffix.lower()
    expected=record.package_manifest.get('files',{}).get(asset_path)
    if not expected or suffix not in {'.js','.mjs','.wasm','.json','.txt','.md'} or not record.artifact: raise Http404('Package asset is unavailable.')
    try:
        with record.artifact.open('rb') as stream, ZipFile(stream) as archive:
            member=archive.getinfo(asset_path)
            if member.file_size>MAX_MEMBER_BYTES: raise Http404('Package asset exceeds the size limit.')
            content=archive.read(member)
        if hashlib.sha256(content).hexdigest()!=expected: raise Http404('Package asset failed its integrity check.')
    except (OSError,ValueError,KeyError,BadZipFile,RuntimeError,NotImplementedError) as error:
        raise Http404('Package asset is unavailable.') from error
    mime={'.wasm':'application/wasm','.json':'application/json','.txt':'text/plain','.md':'text/plain'}.get(suffix,'text/javascript')
    response=HttpResponse(content,content_type=mime)
    response['X-Content-Type-Options']='nosniff'
    response['Cross-Origin-Resource-Policy']='same-origin'
    response['Cache-Control']='private, no-store'
    # Module imports may load this package's assets. Workers cannot use network APIs.
    scope=request.build_absolute_uri(f'/plugins/{record.plugin_id}/assets/')
    response['Content-Security-Policy']=f"default-src 'none'; script-src {scope}; connect-src 'none'; worker-src 'none'; object-src 'none'; base-uri 'none'"
    return response


@login_required(login_url='/mycelium/login')
def sample_package(request):
    manifest={'id':'demo.zip-calculator','name':'ZIP Calculator Example','version':'1.0.0','api_version':'1.0','description':'A small module worker demonstrating the plugin package format. Doubles the supplied value.','type':'javascript','entrypoint':'worker.js'}
    memory=io.BytesIO()
    with ZipFile(memory,'w') as archive:
        archive.writestr('plugin.json',json.dumps(manifest,indent=2))
        archive.writestr('worker.js',"import { calculate } from './lib/calculate.js';\nself.onmessage = ({data}) => {\n  if (data.type === 'initialize') postMessage({type:'ready'});\n  else if (data.type === 'run') postMessage({type:'result',value:calculate(data.value)});\n};\n")
        archive.writestr('lib/calculate.js','export const calculate = value => value * 2;\n')
        archive.writestr('README.md','# Browser plugin example\nSign this package with the local CLI, then upload it through Plugin Manager for administrator review.\nAfter site approval, add it to your personal Plugin Store.\nInstalled Python package code is not accepted in ZIP uploads.\n')
    response=HttpResponse(memory.getvalue(),content_type='application/zip')
    response['Content-Disposition']='attachment; filename="cadevil-plugin-example.zip"'
    response['Cache-Control']='private, no-store'
    response['X-Content-Type-Options']='nosniff'
    return response


@staff_required
def package_download(request,plugin_id):
    from django.http import FileResponse
    record=get_object_or_404(PluginRecord,plugin_id=plugin_id,source='upload',artifact_type='zip')
    try: stream=record.artifact.open('rb')
    except (OSError,ValueError) as error: raise Http404('Package is unavailable.') from error
    response=FileResponse(stream,content_type='application/zip',as_attachment=True,filename=record.plugin_id+'.zip')
    response['Cache-Control']='private, no-store';response['X-Content-Type-Options']='nosniff'
    return response
