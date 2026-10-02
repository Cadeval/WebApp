import base64
import hashlib
import io
import json
import tarfile
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, SimpleTestCase, override_settings
from . import tests as existing_tests
from . import test_store as fixtures
from .models import PluginRecord, PluginSigningKey, PluginActivationError
from .packages import validate_package
from .archives import normalize_archive
from .signatures import canonical_payload, registration_payload, verify_package_signature
from .sign_cli import sign_package, load_private_key


def encrypted_key(private, password):
    public=private.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    salt=b's'*16;iv=b'i'*12
    wrapping=PBKDF2HMAC(algorithm=hashes.SHA256(),length=32,salt=salt,iterations=600000).derive(password.encode())
    encrypted=AESGCM(wrapping).encrypt(iv,private.private_bytes(serialization.Encoding.DER,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()),None)
    enc=lambda value:base64.b64encode(value).decode()
    return {'format':'cadevil-signing-key-v1','algorithm':'Ed25519','key_id':hashlib.sha256(public).hexdigest(),'public_key':enc(public),'encryption':{'algorithm':'AES-256-GCM','kdf':'PBKDF2-SHA256','iterations':600000,'salt':enc(salt),'iv':enc(iv)},'encrypted_private_key':enc(encrypted)}


def tar_package(zip_bytes, mode='w:'):
    stream=io.BytesIO()
    with ZipFile(io.BytesIO(zip_bytes)) as source,tarfile.open(fileobj=stream,mode=mode) as dest:
        for name in source.namelist():
            data=source.read(name);member=tarfile.TarInfo(name);member.size=len(data);dest.addfile(member,io.BytesIO(data))
    return stream.getvalue()


class ArchiveTests(SimpleTestCase):
    def test_formats_preserve_signed_file_hashes(self):
        expected=validate_package(fixtures.package())['files']
        for mode,kind in [('w:','tar'),('w:gz','tar.gz'),('w:xz','tar.xz')]:
            with self.subTest(kind=kind):
                content=tar_package(fixtures.package(),mode)
                self.assertEqual(normalize_archive(content)[1],kind)
                self.assertEqual(validate_package(content)['files'],expected)

    def test_tar_links_traversal_and_oversize_are_rejected(self):
        for name,kind,size in [('../bad.js',tarfile.REGTYPE,1),('link.js',tarfile.SYMTYPE,0),('hard.js',tarfile.LNKTYPE,0),('sparse.js',tarfile.GNUTYPE_SPARSE,0),('huge.txt',tarfile.REGTYPE,2*1024*1024+1)]:
            stream=io.BytesIO()
            with tarfile.open(fileobj=stream,mode='w:') as archive:
                member=tarfile.TarInfo(name);member.type=kind;member.size=size;member.linkname='target';archive.addfile(member,io.BytesIO(b'x'*size))
            with self.subTest(name=name),self.assertRaises(ValidationError):validate_package(stream.getvalue())

    def test_truncated_and_concatenated_compression_is_rejected(self):
        for mode in ['w:gz','w:xz']:
            content=tar_package(fixtures.package(),mode)
            for changed in [content[:-10],content+content]:
                with self.subTest(mode=mode),self.assertRaises(ValidationError):normalize_archive(changed)

    def test_cli_encrypt_decrypt_and_multiple_output_formats(self):
        private=Ed25519PrivateKey.generate();password='fixture-passphrase'
        with TemporaryDirectory() as folder:
            folder=Path(folder);key=folder/'key.json';key.write_text(json.dumps(encrypted_key(private,password)))
            source=folder/'package.zip';source.write_bytes(fixtures.package())
            self.assertEqual(load_private_key(key,password)[0].private_bytes_raw(),private.private_bytes_raw())
            with self.assertRaises(ValueError):load_private_key(key,'wrong')
            for suffix in ['zip','tar','tgz','txz']:
                output=folder/f'signed.{suffix}';sign_package(source,key,output,password)
                manifest=validate_package(output.read_bytes())
                private.public_key().verify(base64.b64decode(manifest['signature']['signature']),canonical_payload(manifest['files']))
                with self.assertRaises(ValueError):sign_package(source,key,output,password)
            with self.assertRaises(ValueError):sign_package(source,key,source,password)


@override_settings(STATIC_URL='/static/')
class SigningTests(TestCase):
    def setUp(self):existing_tests.NativePluginTests.setUp(self)

    def register(self,private=None,change=None):
        private=private or Ed25519PrivateKey.generate()
        response=self.client.get('/plugins/keys/')
        registration=response.context['registration']
        public=base64.b64encode(private.public_key().public_bytes_raw()).decode()
        payload={'label':'My signing key','public_key':public,'challenge':registration['challenge'],'proof':base64.b64encode(private.sign(registration_payload(registration['challenge'],self.staff.pk,public))).decode()}
        payload.update(change or {})
        result=self.client.request('POST','/plugins/keys/register/',content=json.dumps(payload).encode(),content_type='application/json')
        return private,result

    def signed(self,private,key_id):
        stream=io.BytesIO(fixtures.package());files=validate_package(stream.getvalue())['files']
        signature={'format':'cadevil-plugin-signature-v1','algorithm':'Ed25519','key_id':key_id,'signature':base64.b64encode(private.sign(canonical_payload(files))).decode()}
        with ZipFile(stream,'a') as archive:archive.writestr('signature.json',json.dumps(signature))
        return stream.getvalue()

    def upload(self,content,name='plugin.zip'):
        return self.client.post('/plugins/store/upload/',{'artifact':SimpleUploadedFile(name,content)},HTTP_HX_REQUEST='true')

    def test_browser_registration_requires_proof_and_only_stores_public_key(self):
        private,response=self.register();self.assertEqual(response.status_code,201,response.content.decode()[:800])
        key=PluginSigningKey.objects.get();self.assertEqual(key.owner,self.staff)
        self.assertEqual(key.fingerprint,hashlib.sha256(private.public_key().public_bytes_raw()).hexdigest())
        self.assertNotIn('private',','.join(field.name for field in key._meta.fields))
        self.assertEqual(self.register(change={'proof':base64.b64encode(b'x'*64).decode()})[1].status_code,400)
        self.assertEqual(self.register(change={'encrypted_private_key':'not accepted'})[1].status_code,400)
        self.assertEqual(self.register(private)[1].status_code,409)

    def test_csrf_registration_and_anonymous_access(self):
        self.client.auto_csrf=False
        self.assertEqual(self.register()[1].status_code,403)
        self.client.logout()
        for path in ['/plugins/keys/','/plugins/keys/cli.py']:self.assertEqual(self.client.get(path).status_code,302)

    def test_unsigned_tampered_and_unknown_signers_are_rejected(self):
        self.assertEqual(self.upload(fixtures.package()).status_code,400)
        private,result=self.register();signed=self.signed(private,result.json()['key_id'])
        for change in ['file','signature']:
            stream=io.BytesIO();original=ZipFile(io.BytesIO(signed))
            with ZipFile(stream,'w') as archive:
                for name in original.namelist():
                    content=original.read(name)
                    if change=='file' and name=='lib/calc.js':content=b'export const calculate=x=>x*3;'
                    if change=='signature' and name=='signature.json':
                        sig=json.loads(content);sig['key_id']='0'*64;content=json.dumps(sig).encode()
                    archive.writestr(name,content)
            self.assertEqual(self.upload(stream.getvalue()).status_code,400)
        self.assertFalse(PluginRecord.objects.filter(source='upload').exists())

    def test_regular_publisher_own_key_upload_review_and_revoke(self):
        private,result=self.register();key_id=result.json()['key_id'];content=self.signed(private,key_id)
        self.client.logout();self.client.force_login(self.regular);self.client.get("/plugins/keys/")
        self.assertEqual(self.upload(content).status_code,403)
        key=PluginSigningKey.objects.get(fingerprint=key_id);key.owner=self.regular;key.save()
        response=self.upload(tar_package(content,'w:xz'),'signed.txz');self.assertEqual(response.status_code,201,response.content.decode()[:800])
        self.assertContains(response,'administrator must review',status_code=201)
        record=PluginRecord.objects.get(plugin_id='zip.calculator');self.assertFalse(record.enabled);self.assertEqual(record.package_manifest['archive_format'],'tar.xz')
        self.assertEqual(self.client.post('/plugins/store/zip.calculator/enable/').status_code,409)
        self.assertEqual(self.client.post('/plugins/zip.calculator/enable/').status_code,403)
        self.client.logout();self.client.force_login(self.staff);self.client.get("/plugins/keys/");self.assertEqual(self.client.post('/plugins/zip.calculator/enable/').status_code,200)
        self.client.logout();self.client.force_login(self.regular);self.client.get("/plugins/keys/")
        self.assertEqual(self.client.get('/plugins/zip.calculator/assets/worker.js').status_code,404)
        self.assertEqual(self.client.post('/plugins/store/zip.calculator/enable/').status_code,302)
        self.assertEqual(self.client.get('/plugins/zip.calculator/assets/worker.js').status_code,200)
        self.assertEqual(self.client.post(f'/plugins/keys/{key_id}/revoke/').status_code,302)
        record.refresh_from_db();self.assertFalse(record.enabled)
        self.assertEqual(self.client.get('/plugins/zip.calculator/assets/worker.js').status_code,404)
        with self.assertRaises(PluginActivationError):record.set_enabled(True)
        with self.assertRaises(ValidationError):verify_package_signature(validate_package(content))

    def test_other_user_cannot_revoke_and_admin_can_download_disabled_package(self):
        private,result=self.register();key_id=result.json()['key_id'];self.upload(self.signed(private,key_id))
        self.assertEqual(self.client.get('/plugins/zip.calculator/package.zip').status_code,200)
        self.client.logout();self.client.force_login(self.regular);self.client.get("/plugins/keys/")
        self.assertEqual(self.client.post(f'/plugins/keys/{key_id}/revoke/').status_code,403)
        self.assertEqual(self.client.get('/plugins/zip.calculator/package.zip').status_code,403)
        self.assertIsNone(PluginSigningKey.objects.get().revoked_at)
