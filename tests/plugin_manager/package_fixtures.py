"""Signed archive fixtures shared by the plugin integration tests."""
import base64
import hashlib
import io
import json
from zipfile import ZipFile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.contrib.auth import get_user_model
from plugin_manager.models import PluginSigningKey
from plugin_manager.signatures import canonical_payload


def signed_package(owner=None, plugin_id='uploaded-test', *, wasm=False):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes_raw()
    fingerprint = hashlib.sha256(public).hexdigest()
    if owner is None:
        owner = get_user_model().objects.create_user(username='signer-'+fingerprint[:16])
    PluginSigningKey.objects.create(owner=owner, label='Fixture key', fingerprint=fingerprint, public_key=base64.b64encode(public).decode())
    entry = 'calculator.wasm' if wasm else 'worker.js'
    files = {
        'plugin.json': json.dumps({'id':plugin_id,'name':'Uploaded test','version':'1.0.0','api_version':'1.0','type':'wasm' if wasm else 'javascript','entrypoint':entry}).encode(),
        entry: bytes.fromhex('0061736d0100000001060160017c017c03020100070d010963616c63756c61746500000a10010e002000440000000000000040a20b') if wasm else b"self.onmessage=({data})=>{if(data.type==='initialize')postMessage({type:'ready'});else if(data.type==='run')postMessage({type:'result',value:data.value*2});};",
    }
    hashes = {name:hashlib.sha256(content).hexdigest() for name,content in files.items()}
    files['signature.json'] = json.dumps({'format':'cadevil-plugin-signature-v1','algorithm':'Ed25519','key_id':fingerprint,'signature':base64.b64encode(private.sign(canonical_payload(hashes))).decode()}).encode()
    stream = io.BytesIO()
    with ZipFile(stream,'w') as archive:
        for name,content in files.items():archive.writestr(name,content)
    return stream.getvalue()
