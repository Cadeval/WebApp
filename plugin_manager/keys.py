"""Register public signing keys; private keys are created and encrypted in the browser."""
import hashlib
import json
import logging
import secrets
import time
from pathlib import Path
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from .models import PluginSigningKey, PluginCertificateAuthority
from .signatures import decode, registration_payload
from .teams import can_manage_key, can_manage_team, visible_teams
from .certificate_authority import issue_key_certificate, certificate_chain, revoke_key, CertificateAuthorityUnavailable

logger = logging.getLogger('cadevil.security')


def signing_key_context(request):
    challenge=secrets.token_urlsafe(32)
    request.session['plugin_key_challenge']={'value':challenge,'issued':time.time()}
    from django.db.models import Q
    teams=list(visible_teams(request.user))
    keys=list(PluginSigningKey.objects.filter(Q(owner=request.user,team__isnull=True)|Q(team__in=teams)).select_related('team','certificate_authority').distinct())
    from .certificate_authority import trusted_key, certificate
    for key in keys:
        key.may_revoke=can_manage_key(request.user,key)
        key.trusted=trusted_key(key)
        try: key.certificate_expires=certificate(key.certificate).not_valid_after_utc if key.certificate else None
        except ValidationError: key.certificate_expires=None
    from .teams import member_of
    return {'signing_keys':keys,'signing_teams':[team for team in teams if can_manage_team(request.user,team) and member_of(request.user,team)],
            'certificate_roots':list(PluginCertificateAuthority.objects.all()) if request.user.is_staff and request.user.has_perm('plugin_manager.view_plugincertificateauthority') else [],
            'may_revoke_certificate_roots':request.user.is_staff and request.user.has_perm('plugin_manager.change_plugincertificateauthority'),
            'registration':{'challenge':challenge,'owner':str(request.user.pk)}}


@login_required(login_url='/mycelium/login')
def signing_keys_page(request,notice='',status=200):
    url = '/mycelium/settings?section=security'
    if request.headers.get('HX-Request') == 'true':
        from mycelium.settings_views import settings_response
        response = settings_response(request, section='security', notice=notice, status=status)
        response['HX-Replace-Url'] = url
        return response
    return redirect(url)


@login_required(login_url='/mycelium/login')
def register_signing_key(request):
    try:
        if len(request.body)>4096: raise ValidationError('The registration request is too large.')
        payload=json.loads(request.body)
        if not isinstance(payload,dict) or not {'label','public_key','proof','challenge'}<=set(payload) or set(payload)-{'label','public_key','proof','challenge','team_id'}: raise ValidationError('Invalid key registration request.')
        label=payload['label']
        if not isinstance(label,str) or not 1<=len(label.strip())<=120: raise ValidationError('Choose a key name between 1 and 120 characters.')
        challenge=request.session.get('plugin_key_challenge',{})
        if not isinstance(payload['challenge'],str) or challenge.get('value')!=payload['challenge'] or time.time()-challenge.get('issued',0)>600:
            raise ValidationError('Key registration expired. Reload this page and try again.')
        public=decode(payload['public_key'],32)
        team_id=payload.get('team_id') or None
        if team_id is not None and not isinstance(team_id,str): raise ValidationError('Choose a valid team.')
        Ed25519PublicKey.from_public_bytes(public).verify(decode(payload['proof'],64),registration_payload(payload['challenge'],request.user.pk,payload['public_key'],team_id=team_id))
        with transaction.atomic():
            team=None
            if team_id:
                from .models import Team
                from .teams import member_of
                team=get_object_or_404(Team.objects.select_for_update(),pk=team_id)
                if not can_manage_team(request.user,team) or not member_of(request.user,team): raise PermissionDenied('Only a current member with team management permission may register a team signing key.')
            key=PluginSigningKey.objects.create(owner=request.user,team=team,label=label.strip(),fingerprint=hashlib.sha256(public).hexdigest(),public_key=payload['public_key'])
            issue_key_certificate(key)
        request.session.pop('plugin_key_challenge',None)
        logger.info('Public signing key registered', extra={'event': 'signing_key_registered', 'outcome': 'completed'})
        return JsonResponse({'key_id':key.fingerprint,'label':key.label,'certificate_chain':certificate_chain(key),'scope':'team' if key.team_id else 'personal','team_id':str(key.team_id) if key.team_id else None},status=201)
    except CertificateAuthorityUnavailable as error:
        return JsonResponse({'error':' '.join(error.messages)},status=503)
    except (ValidationError,InvalidSignature,ValueError,TypeError,KeyError) as error:
        logger.warning('Signing key registration rejected', extra={
            'event': 'signing_key_registration_rejected', 'outcome': 'rejected', 'error_type': type(error).__name__})
        message='Proof of key ownership is invalid.' if isinstance(error,InvalidSignature) else (' '.join(error.messages) if isinstance(error,ValidationError) else 'Invalid key registration request.')
        return JsonResponse({'error':message},status=400)
    except IntegrityError:
        logger.warning('Signing key registration conflicts with an existing key', extra={
            'event': 'signing_key_registration_rejected', 'outcome': 'conflict'})
        return JsonResponse({'error':'This public key is already registered.'},status=409)


@login_required(login_url='/mycelium/login')
def revoke_signing_key(request,key_id):
    with transaction.atomic():
        key=get_object_or_404(PluginSigningKey.objects.select_for_update(),fingerprint=key_id)
        if not can_manage_key(request.user,key): raise PermissionDenied('Only the key owner, a permitted team manager or an administrator can revoke this key.')
        revoke_key(key)
        transaction.on_commit(lambda: logger.info('Public signing key revoked', extra={
            'event': 'signing_key_revoked', 'outcome': 'completed'}))
    if request.headers.get('HX-Request')!='true': return redirect('/mycelium/settings?section=security')
    from mycelium.settings_views import settings_response
    response=settings_response(request,section='security',notice='Key revoked. Packages signed with it are disabled.')
    response['HX-Push-Url']='false'
    return response


@login_required(login_url='/mycelium/login')
def renew_signing_key(request,key_id):
    try:
        with transaction.atomic():
            key=get_object_or_404(PluginSigningKey.objects.select_for_update(),fingerprint=key_id)
            if not can_manage_key(request.user,key): raise PermissionDenied('Only the owner or a permitted manager may renew this certificate.')
            if key.revoked_at: raise ValidationError('Revoked keys cannot be renewed. Create a new key.')
            if key.certificate_authority_id and key.certificate_serial:
                from .models import SigningCertificateRevocation
                SigningCertificateRevocation.objects.get_or_create(authority=key.certificate_authority,serial=key.certificate_serial,defaults={'reason':'Certificate renewed'})
            issue_key_certificate(key)
        # Only public certificates are returned; callers update their local
        # encrypted key bundle before signing another package.
        response=JsonResponse({'key_id':key.fingerprint,'certificate_chain':certificate_chain(key)})
        response['Content-Disposition']=f'attachment; filename="{key.fingerprint[:16]}.cadevil-certificate.json"'
        response['Cache-Control']='private, no-store'
        response['X-Content-Type-Options']='nosniff'
        return response
    except ValidationError as error:
        return JsonResponse({'error':' '.join(error.messages)},status=503 if isinstance(error,CertificateAuthorityUnavailable) else 400)


@login_required(login_url='/mycelium/login')
def signing_cli(request):
    source=Path(__file__).with_name('sign_cli.py').read_bytes()
    response=HttpResponse(source,content_type='text/x-python')
    response['Content-Disposition']='attachment; filename="cadevil_sign.py"'
    response['Cache-Control']='private, no-store'
    response['X-Content-Type-Options']='nosniff'
    return response
