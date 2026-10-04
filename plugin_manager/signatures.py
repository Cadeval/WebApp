"""Ed25519 signatures bind every package file to a registered public key."""
import base64
import json
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from django.core.exceptions import ValidationError


def canonical_payload(files):
    return json.dumps({'context':'cadevil-plugin-package-v1','files':files},sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')


def decode(value,length):
    try:
        if not isinstance(value,str): raise ValueError()
        data=base64.b64decode(value,validate=True)
        if len(data)!=length: raise ValueError()
        return data
    except (ValueError,TypeError) as error: raise ValidationError('Invalid Ed25519 key or signature encoding.') from error


def verify_package_signature(manifest):
    from .models import PluginSigningKey
    signature=manifest.get('signature')
    if not isinstance(signature,dict) or set(signature)!={'format','algorithm','key_id','signature'} or signature.get('format')!='cadevil-plugin-signature-v1' or signature.get('algorithm')!='Ed25519':
        raise ValidationError('ZIP packages must be signed with the local signing CLI before uploading.')
    identifier=signature.get('key_id')
    if not isinstance(identifier,str) or len(identifier)!=64: raise ValidationError('Invalid signing key id.')
    key=PluginSigningKey.objects.filter(fingerprint=identifier,revoked_at__isnull=True,owner__isnull=False).first()
    if not key: raise ValidationError('The signing key is unknown or revoked. Register a key in the web interface first.')
    try:
        Ed25519PublicKey.from_public_bytes(decode(key.public_key,32)).verify(decode(signature['signature'],64),canonical_payload(manifest['files']))
    except (InvalidSignature,ValueError) as error: raise ValidationError('The package signature is invalid. Package contents changed or the wrong key was used.') from error
    return key


def registration_payload(challenge,owner,public_key):
    return json.dumps({'challenge':challenge,'context':'cadevil-key-registration-v1','owner':str(owner),'public_key':public_key},sort_keys=True,separators=(',',':')).encode('ascii')
