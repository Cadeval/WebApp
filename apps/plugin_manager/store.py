"""Local plugin catalog and protected package assets."""
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
from django.urls import reverse

from apps.shared.page_views import render_page
from apps.shared.services import staff_required
from .forms import PluginUploadForm
from .models import PluginRecord, PluginActivationError
from .services import create_uploaded_plugin, manage_plugin
from .packages import safe_path, MAX_MEMBER_BYTES

BUILTINS={
    'cadevil.bim.model_manager':('BIM Workspace','Manage IFC models, reference configurations, material passports and comparisons.','/plugins/bim/model_manager/'),
    'cadevil.example.editor':('Rust IFC Editor','Inspect and edit a local IFC file in a Rust/WebAssembly worker.','/plugins/ifc-editor/'),
    'cadevil.rust-example.editor':('Rust Snake','Play the bundled game powered by a Rust/WebAssembly worker.','/plugins/rust-snake/'),
}


def store_response(request, *, form=None, notice='', status=200):
    query=request.GET.get('q','').strip()[:200]
    catalog=[]
    for record in PluginRecord.objects.select_related("signing_key__owner").all():
        builtin=BUILTINS.get(record.plugin_id)
        description=(builtin[1] if builtin else record.package_manifest.get('description','')) or ('Installed Python package. Server installation and code updates require a restart.' if record.source=='package' else 'Reviewed browser plugin, available in the Configuration Editor when enabled.')
        if query and query.casefold() not in (' '.join([record.plugin_id,record.name,description])).casefold(): continue
        catalog.append({'record':record,'description':description,'url':builtin[2] if builtin else '/plugins/bim/config_editor/','category':'Bundled tool' if builtin else ('Installed Python package' if record.source=='package' else 'Uploaded browser plugin')})
    response=render_page(request,'plugin_manager/store.html',{'catalog':catalog,'query':query,'notice':notice,'upload_form':form or PluginUploadForm()},status=status)
    if request.method=='POST': response['HX-Push-Url']='false'
    return response


@login_required(login_url='/mycelium/login')
def plugin_store(request): return store_response(request)


@login_required(login_url='/mycelium/login')
def store_upload(request):
    form=PluginUploadForm(request.POST,request.FILES)
    if not form.is_valid(): return store_response(request,form=form,status=400)
    artifact=form.cleaned_data["artifact"]
    if not request.user.is_staff and (artifact.signing_key.owner_id!=request.user.pk):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("Publish a package signed with one of your own keys.")
    try: record=create_uploaded_plugin(form,request.user)
    except IntegrityError:
        form.add_error('artifact','A plugin with this id was installed while the upload was being processed. Choose a different id.')
        return store_response(request,form=form,status=409)
    if request.headers.get('HX-Request')!='true': return redirect('plugin_manager:plugin_store')
    return store_response(request,notice=f'{record.name} uploaded. ' + ('Review its code and enable it when ready.' if request.user.is_staff else 'An administrator must review and enable it.'),status=201)


@staff_required
def store_action(request,plugin_id,action):
    get_object_or_404(PluginRecord,plugin_id=plugin_id)
    if action not in {'enable','disable'}: raise Http404('Unknown plugin store action.')
    try: manage_plugin(plugin_id,action)
    except PluginActivationError as error: return store_response(request,notice=str(error),status=409)
    if request.headers.get('HX-Request')!='true': return redirect('plugin_manager:plugin_store')
    return store_response(request,notice='Plugin enabled.' if action=='enable' else 'Plugin disabled.')


@login_required(login_url='/mycelium/login')
def package_asset(request,plugin_id,asset_path):
    record=get_object_or_404(PluginRecord,plugin_id=plugin_id,source='upload',artifact_type='zip',enabled=True,error='',signing_key__isnull=False,signing_key__revoked_at__isnull=True,signing_key__owner__isnull=False)
    try: safe_path(asset_path)
    except ValidationError as error: raise Http404('Package asset is unavailable.') from error
    suffix=PurePosixPath(asset_path).suffix.lower()
    expected=record.package_manifest.get('files',{}).get(asset_path)
    if not expected or suffix not in {'.js','.mjs','.wasm'} or not record.artifact: raise Http404('Package asset is unavailable.')
    try:
        with record.artifact.open('rb') as stream, ZipFile(stream) as archive:
            member=archive.getinfo(asset_path)
            if member.file_size>MAX_MEMBER_BYTES: raise Http404('Package asset exceeds the size limit.')
            content=archive.read(member)
        if hashlib.sha256(content).hexdigest()!=expected: raise Http404('Package asset failed its integrity check.')
    except (OSError,ValueError,KeyError,BadZipFile,RuntimeError,NotImplementedError) as error:
        raise Http404('Package asset is unavailable.') from error
    response=HttpResponse(content,content_type='application/wasm' if suffix=='.wasm' else 'text/javascript')
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
        archive.writestr('README.md','# Browser plugin example\nSign this package with the local CLI, then upload it through the plugin store for administrator review.\nInstalled Python package code is not accepted in ZIP uploads.\n')
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
