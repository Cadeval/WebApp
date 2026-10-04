"""Generate public test evidence only; CA private keys remain in a temporary directory."""


def main():
    import os, sys, json, base64
    from pathlib import Path
    os.environ['DJANGO_SETTINGS_MODULE']='tests.passport_test_settings'
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
    import django
    django.setup()
    from django.core.management import call_command
    call_command('migrate',verbosity=0)
    from plugin_manager import certificate_authority as ca
    from plugin_manager.models import SigningCertificateRevocation, PluginSigningKey
    from django.contrib.auth import get_user_model
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from hashlib import sha256
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.x509.oid import ExtendedKeyUsageOID
    from django.utils import timezone
    from datetime import timedelta
    authority=ca.initialize_ca()
    files={'worker.js':b"import {value} from './dep.js'; self.onmessage=()=>postMessage(value);",'dep.js':b'export const value=42;'}
    bundle=ca.bundled_trust('cadevil.example.editor',files)
    bundle.update(urls={name:'/plugins/cadevil.example.editor/assets/'+name for name in files},entrypoint='worker.js',wasm='')
    roots={'format':'cadevil-plugin-roots-x509-v1',**ca.freshness(),'roots':[{'id':authority.fingerprint,'certificate':authority.certificate}]}
    leaf=ca.certificate(authority.publisher_certificate)
    SigningCertificateRevocation.objects.create(authority=authority,serial=format(leaf.serial_number,'x'),reason='Public test revocation')
    revoked_crl=ca.encode(ca.crl(authority))
    def altered_leaf(eku=None,usage=None):
        builder=x509.CertificateBuilder().subject_name(leaf.subject).issuer_name(leaf.issuer).public_key(leaf.public_key()).serial_number(x509.random_serial_number()).not_valid_before(leaf.not_valid_before_utc).not_valid_after(leaf.not_valid_after_utc)
        for ext in leaf.extensions:
            value=eku if ext.oid==x509.ExtensionOID.EXTENDED_KEY_USAGE and eku else usage if ext.oid==x509.ExtensionOID.KEY_USAGE and usage else ext.value
            builder=builder.add_extension(value,ext.critical)
        return ca.encode(builder.sign(ca.private_key(authority,'issuer'),hashes.SHA256()).public_bytes(serialization.Encoding.DER))
    private=Ed25519PrivateKey.generate();public=private.public_key().public_bytes_raw()
    key=PluginSigningKey.objects.create(owner=get_user_model().objects.create_user(username='public-fixture-owner'),label='Public test publisher',fingerprint=sha256(public).hexdigest(),public_key=ca.encode(public))
    ca.issue_key_certificate(key)
    from plugin_manager.signatures import canonical_payload
    user_bundle={**ca.trust_bundle(key),'plugin_id':'uploaded.fixture','files':bundle['files'],'signature':{'format':'cadevil-plugin-signature-v1','algorithm':'Ed25519','key_id':key.fingerprint,'signature':ca.encode(private.sign(canonical_payload(bundle['files'])))}}
    fixture={'now':timezone.now().isoformat(),'bundle':bundle,'roots':roots,'files':{name:base64.b64encode(data).decode() for name,data in files.items()},'revoked_crl':revoked_crl,'user_bundle':user_bundle,
     'wrong_eku_leaf':altered_leaf(eku=x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH])),
     'wrong_usage_leaf':altered_leaf(usage=x509.KeyUsage(digital_signature=False,content_commitment=False,key_encipherment=True,data_encipherment=False,key_agreement=False,key_cert_sign=False,crl_sign=False,encipher_only=False,decipher_only=False))}
    Path(__file__).resolve().parents[1].joinpath('browser/js/fixtures/plugin-trust.json').write_text(json.dumps(fixture,indent=2)+'\n')


if __name__ == '__main__':
    main()
