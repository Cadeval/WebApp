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
from .models import PluginSigningKey
from .signatures import decode, registration_payload

logger = logging.getLogger('cadevil.security')


def signing_key_context(request):
    challenge=secrets.token_urlsafe(32)
    request.session['plugin_key_challenge']={'value':challenge,'issued':time.time()}
    return {'signing_keys':PluginSigningKey.objects.filter(owner=request.user),
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
        if not isinstance(payload,dict) or set(payload)!={'label','public_key','proof','challenge'}: raise ValidationError('Invalid key registration request.')
        label=payload['label']
        if not isinstance(label,str) or not 1<=len(label.strip())<=120: raise ValidationError('Choose a key name between 1 and 120 characters.')
        challenge=request.session.get('plugin_key_challenge',{})
        if not isinstance(payload['challenge'],str) or challenge.get('value')!=payload['challenge'] or time.time()-challenge.get('issued',0)>600:
            raise ValidationError('Key registration expired. Reload this page and try again.')
        public=decode(payload['public_key'],32)
        Ed25519PublicKey.from_public_bytes(public).verify(decode(payload['proof'],64),registration_payload(payload['challenge'],request.user.pk,payload['public_key']))
        key=PluginSigningKey.objects.create(owner=request.user,label=label.strip(),fingerprint=hashlib.sha256(public).hexdigest(),public_key=payload['public_key'])
        request.session.pop('plugin_key_challenge',None)
        logger.info('Public signing key registered', extra={'event': 'signing_key_registered', 'outcome': 'completed'})
        return JsonResponse({'key_id':key.fingerprint,'label':key.label},status=201)
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
        if key.owner_id!=request.user.pk and not (request.user.is_active and request.user.is_staff): raise PermissionDenied('Only the key owner or an administrator can revoke this key.')
        if not key.revoked_at:
            key.revoked_at=timezone.now();key.save(update_fields=['revoked_at'])
            key.plugins.update(enabled=False,error='The package signing key was revoked. Re-sign and upload under an active key.')
            transaction.on_commit(lambda: logger.info('Public signing key revoked', extra={
                'event': 'signing_key_revoked', 'outcome': 'completed'}))
    if request.headers.get('HX-Request')!='true': return redirect('/mycelium/settings?section=security')
    from mycelium.settings_views import settings_response
    response=settings_response(request,section='security',notice='Key revoked. Packages signed with it are disabled.')
    response['HX-Push-Url']='false'
    return response


@login_required(login_url='/mycelium/login')
def signing_cli(request):
    source=Path(__file__).with_name('sign_cli.py').read_bytes()
    response=HttpResponse(source,content_type='text/x-python')
    response['Content-Disposition']='attachment; filename="cadevil_sign.py"'
    response['Cache-Control']='private, no-store'
    response['X-Content-Type-Options']='nosniff'
    return response
