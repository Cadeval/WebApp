"""Public X.509 trust documents and permission-checked package/key metadata."""
from datetime import timedelta
from cryptography.hazmat.primitives import serialization
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from .certificate_authority import certificate, crl, trust_bundle, revoke_authority
from .models import PluginCertificateAuthority, PluginRecord, PluginSigningKey
from .teams import can_manage_key, member_of, public_key_data
from .workflows import workflow_plugin_enabled
from urllib.parse import quote


def fresh_response(data,*,status=200):
    response=JsonResponse(data,status=status);response['Cache-Control']='private, no-store';response['X-Content-Type-Options']='nosniff'
    return response


def roots(request):
    now=timezone.now()
    authorities=PluginCertificateAuthority.objects.filter(revoked_at__isnull=True)
    roots=[]
    for authority in authorities:
        cert=certificate(authority.certificate)
        if cert.not_valid_before_utc<=now<cert.not_valid_after_utc: roots.append({'id':authority.fingerprint,'certificate':authority.certificate})
    return fresh_response({'format':'cadevil-plugin-roots-x509-v1','roots':roots,'generated_at':now.isoformat(),'expires_at':(now+timedelta(seconds=300)).isoformat()})


def authority_document(request,root_id,document):
    authority=get_object_or_404(PluginCertificateAuthority,fingerprint=root_id)
    try:
        if document in {'root.der','intermediate.der','root.pem','intermediate.pem'}:
            pem=document.endswith('.pem')
            content=certificate(authority.certificate if document.startswith('root.') else authority.intermediate_certificate).public_bytes(serialization.Encoding.PEM if pem else serialization.Encoding.DER)
            mime='application/x-pem-file' if pem else 'application/pkix-cert'
        elif document in {'leaf.crl','issuer.crl'}:
            content=crl(authority,issuer=document=='issuer.crl');mime='application/pkix-crl'
        else: raise Http404('Unknown public CA document.')
    except ValidationError:
        return fresh_response({'error':'Fresh CA evidence is unavailable.'},status=503)
    response=HttpResponse(content,content_type=mime);response['Cache-Control']='no-store';response['X-Content-Type-Options']='nosniff'
    return response


@login_required(login_url='/mycelium/login')
def public_key(request,key_id):
    key=_readable_key(request,key_id)
    return fresh_response(public_key_data(key))


def _readable_key(request,key_id):
    key=get_object_or_404(PluginSigningKey.objects.select_related('team','certificate_authority'),fingerprint=key_id)
    if key.team_id:
        allowed=member_of(request.user,key.team) or can_manage_key(request.user,key)
    else: allowed=key.owner_id==request.user.pk or can_manage_key(request.user,key)
    if not allowed: raise PermissionDenied('This public key belongs to another account or team.')
    return key


@login_required(login_url='/mycelium/login')
def public_certificate(request,key_id,encoding):
    key=_readable_key(request,key_id)
    if encoding not in {'pem','der'} or not key.certificate: raise Http404('Signing certificate is unavailable.')
    content=certificate(key.certificate).public_bytes(serialization.Encoding.PEM if encoding=='pem' else serialization.Encoding.DER)
    response=HttpResponse(content,content_type='application/x-pem-file' if encoding=='pem' else 'application/pkix-cert')
    response['Content-Disposition']=f'attachment; filename="{key.fingerprint[:16]}.certificate.{encoding}"'
    response['Cache-Control']='private, no-store';response['X-Content-Type-Options']='nosniff'
    return response


@login_required(login_url='/mycelium/login')
def package_trust(request,plugin_id):
    if not workflow_plugin_enabled(request.user,plugin_id): raise Http404('This plugin is not enabled for your workflow.')
    record=get_object_or_404(PluginRecord.objects.select_related('signing_key__certificate_authority','signing_key__owner','signing_key__team'),plugin_id=plugin_id)
    try:
        if record.source==PluginRecord.Source.PACKAGE:
            from .browser_artifacts import bundled_plugin_trust
            data=bundled_plugin_trust(plugin_id)
        else:
            manifest=record.package_manifest
            data={**trust_bundle(record.signing_key),'plugin_id':plugin_id,'files':manifest['files'],'signature':manifest['signature'],
                  'urls':{name:f'/plugins/{quote(plugin_id,safe="")}/assets/{quote(name,safe="/")}' for name in manifest['files']},
                  'entrypoint':manifest['entrypoint'],'wasm':manifest['entrypoint'] if manifest['type']=='wasm' else ''}
            if manifest['type']=='wasm':
                from .browser_artifacts import bundled_wasm_wrapper_trust
                data['wrapper']=bundled_wasm_wrapper_trust()
        return fresh_response(data)
    except (ValidationError,KeyError,ValueError) as error:
        return fresh_response({'error':'Plugin certificate or revocation evidence is unavailable. Ask an administrator to restore its signing trust.'},status=503)


@login_required(login_url='/mycelium/login')
def revoke_root(request,root_id):
    if not request.user.is_active or not request.user.is_staff or not request.user.has_perm('plugin_manager.change_plugincertificateauthority'):
        raise PermissionDenied('Certificate authority revocation requires an administrator with the CA change permission.')
    authority=get_object_or_404(PluginCertificateAuthority,fingerprint=root_id)
    revoke_authority(authority)
    if request.POST.get('return_to')=='settings': return redirect('/mycelium/settings?section=security')
    return fresh_response({'revoked':True,'root_id':root_id})
