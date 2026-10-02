import io
import json
import stat
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from django.test import SimpleTestCase, TestCase, override_settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from .packages import validate_package
from .models import PluginRecord, PluginSigningKey, UserPluginSelection
from .context_processors import _uploaded_editor_items
from . import tests as existing_tests
from .signatures import canonical_payload
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
import base64
import hashlib

MANIFEST={'id':'zip.calculator','name':'ZIP Calculator','version':'1.2.3','api_version':'1.0','description':'A packaged worker with a helper module.','type':'javascript','entrypoint':'worker.js'}
JS=b"import {calculate} from './lib/calc.js'; self.onmessage=({data})=>{if(data.type==='initialize')postMessage({type:'ready'});else if(data.type==='run')postMessage({type:'result',value:calculate(data.value)});};"


def package(manifest=None, extra=None, *, compression=0):
    stream=io.BytesIO()
    with ZipFile(stream,'w',compression=compression) as archive:
        archive.writestr('plugin.json',json.dumps(MANIFEST if manifest is None else manifest))
        archive.writestr('worker.js',JS)
        archive.writestr('lib/calc.js','export const calculate=x=>x*2;')
        for name,data in (extra or {}).items(): archive.writestr(name,data)
    return stream.getvalue()


class PackageValidationTests(SimpleTestCase):
    def test_valid_worker_package_and_nested_modules(self):
        result=validate_package(package())
        self.assertEqual(result['id'],'zip.calculator');self.assertIn('lib/calc.js',result['files'])

    def test_valid_wasm_package(self):
        manifest={**MANIFEST,'type':'wasm','entrypoint':'calculator.wasm'}
        self.assertEqual(validate_package(package(manifest,{'calculator.wasm':b'\x00asm\x01\x00\x00\x00'}))['type'],'wasm')

    def test_unsafe_paths_and_server_code_are_rejected(self):
        for name in ['../escape.js','/absolute.js','C:/escape.js','lib\\escape.js','%2e%2e/escape.js','server.py','page.html']:
            with self.subTest(name=name),self.assertRaises(ValidationError): validate_package(package(extra={name:b'content'}))

    def test_symlink_rejected(self):
        stream=io.BytesIO(package())
        with ZipFile(stream,'a') as archive:
            entry=ZipInfo('link.js');entry.create_system=3;entry.external_attr=(stat.S_IFLNK|0o777)<<16
            archive.writestr(entry,'/private/file')
        with self.assertRaises(ValidationError): validate_package(stream.getvalue())

    def test_missing_wrong_and_incompatible_manifest(self):
        for manifest in [[],{**MANIFEST,'entrypoint':'missing.js'},{**MANIFEST,'api_version':'2.0'},{**MANIFEST,'id':'../bad'},{**MANIFEST,'type':'python'},{**MANIFEST,'priority':10}]:
            with self.subTest(manifest=manifest),self.assertRaises(ValidationError): validate_package(package(manifest))
        with self.assertRaises(ValidationError): validate_package(b'not a zip')
        stream=io.BytesIO()
        with ZipFile(stream,'w') as archive: archive.writestr('worker.js',JS)
        with self.assertRaises(ValidationError): validate_package(stream.getvalue())

    def test_duplicates_and_expansion_bombs_are_rejected(self):
        stream=io.BytesIO(package())
        with ZipFile(stream,'a') as archive:archive.writestr('WORKER.js',JS)
        with self.assertRaises(ValidationError): validate_package(stream.getvalue())
        with self.assertRaises(ValidationError): validate_package(package(extra={'huge.txt':b'a'*(2*1024*1024+1)},compression=ZIP_DEFLATED))
        with self.assertRaises(ValidationError): validate_package(package(extra={f'{i}.txt':b'a' for i in range(33)}))


@override_settings(STATIC_URL='/static/')
class PluginStoreTests(TestCase):
    def setUp(self):
        existing_tests.NativePluginTests.setUp(self)
        self.private=Ed25519PrivateKey.generate()
        public=self.private.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        self.key=PluginSigningKey.objects.create(owner=self.staff,label='Test key',fingerprint=hashlib.sha256(public).hexdigest(),public_key=base64.b64encode(public).decode())

    def upload_zip(self, manifest=None, *, endpoint='/plugins/store/upload/', extra=None, data=None):
        content=package(manifest,extra)
        try:
            files=validate_package(content)['files']
            signature={'format':'cadevil-plugin-signature-v1','algorithm':'Ed25519','key_id':self.key.fingerprint,'signature':base64.b64encode(self.private.sign(canonical_payload(files))).decode()}
            stream=io.BytesIO(content)
            with ZipFile(stream,'a') as archive: archive.writestr('signature.json',json.dumps(signature))
            content=stream.getvalue()
        except ValidationError: pass
        return self.client.post(endpoint,{**(data or {}),'artifact':SimpleUploadedFile('plugin.zip',content,content_type='application/zip')},HTTP_HX_REQUEST='true')

    def test_store_page_full_fragment_search_and_user_permissions(self):
        record=PluginRecord.objects.create(plugin_id='catalog.example',name='Searchable tool',enabled=True)
        self.assertContains(self.client.get('/plugins/manage/'),'Searchable tool')
        self.assertNotContains(self.client.get('/plugins/store/'),'Searchable tool')
        UserPluginSelection.objects.create(user=self.staff,plugin=record)
        self.assertContains(self.client.get('/plugins/store/'),'Plugin Store')
        fragment=self.client.get('/plugins/store/',HTTP_HX_REQUEST='true')
        self.assertNotContains(fragment,'<html');self.assertContains(fragment,'id="content-container"',count=1)
        self.assertContains(self.client.get('/plugins/store/',{'q':'Searchable'}),'Searchable tool')
        self.assertNotContains(self.client.get('/plugins/store/',{'q':'Missing'}),'Searchable tool')
        self.client.logout();self.client.force_login(self.regular)
        self.assertContains(self.client.get('/plugins/manage/'),'Searchable tool')
        self.assertNotContains(self.client.get('/plugins/store/'),'Searchable tool')
        self.assertContains(self.client.get('/plugins/manage/'),'Signed plugin package')
        self.assertEqual(self.upload_zip().status_code,403)
        self.assertEqual(self.client.post('/plugins/store/catalog.example/enable/',HTTP_HX_REQUEST='true').status_code,200)
        self.assertContains(self.client.get('/plugins/store/'),'Searchable tool')
        self.client.logout();self.assertEqual(self.client.get('/plugins/store/').status_code,302)

    def test_archive_only_form_and_raw_file_rejection_on_both_upload_routes(self):
        from .forms import PluginUploadForm
        self.assertEqual(list(PluginUploadForm().fields),['artifact'])
        response=self.client.get('/plugins/manage/')
        self.assertNotContains(response,'name="plugin_id"')
        self.assertNotContains(response,'name="name"')
        self.assertContains(response,'Signed plugin package')
        self.assertNotContains(self.client.get('/plugins/store/'),'name="artifact"')
        for route in ['/plugins/store/upload/','/plugins/upload/']:
            for filename,content,mime in [('worker.js',JS,'text/javascript'),('worker.mjs',JS,'text/javascript'),('calculator.wasm',b'\x00asm\x01\x00\x00\x00','application/wasm')]:
                response=self.client.post(route,{'artifact':SimpleUploadedFile(filename,content,content_type=mime)},HTTP_HX_REQUEST='true')
                self.assertContains(response,'Single JavaScript and WASM files are not accepted',status_code=400)
        self.assertFalse(PluginRecord.objects.filter(source='upload').exists())

    def test_legacy_single_file_endpoint_and_editor_contribution_are_removed(self):
        legacy=PluginRecord.objects.create(plugin_id='legacy',source='upload',artifact_type='js',enabled=True,artifact='plugins/old.js')
        self.assertEqual(self.client.get('/plugins/legacy/artifact/').status_code,404)
        self.assertEqual(_uploaded_editor_items(self.staff),[])
        from .models import PluginActivationError
        with self.assertRaises(PluginActivationError):legacy.set_enabled(True)

    def test_zip_upload_manifest_metadata_and_private_storage(self):
        response=self.upload_zip();self.assertEqual(response.status_code,201)
        record=PluginRecord.objects.get(plugin_id='zip.calculator')
        self.assertFalse(record.enabled);self.assertEqual(record.artifact_type,'zip');self.assertEqual(record.version,'1.2.3')
        self.assertEqual(record.package_manifest['entrypoint'],'worker.js')
        self.assertContains(response,'Review its code',status_code=201)
        self.assertFalse(Path(record.artifact.path).is_relative_to((Path(self.folder.name)/'media').resolve()))

    def test_enable_and_nested_assets_and_editor_context(self):
        self.upload_zip()
        path='/plugins/zip.calculator/assets/lib/calc.js'
        self.assertEqual(self.client.get(path).status_code,404)
        self.assertEqual(self.client.post('/plugins/store/zip.calculator/enable/',HTTP_HX_REQUEST='true').status_code,409)
        self.assertEqual(self.client.post('/plugins/zip.calculator/enable/').status_code,200)
        response=self.client.post('/plugins/store/zip.calculator/enable/',HTTP_HX_REQUEST='true')
        self.assertContains(response,'Plugin enabled for your workflow.');self.assertContains(response,'hx-swap-oob="outerHTML"')
        self.assertEqual(response['HX-Push-Url'],'false')
        response=self.client.get(path);self.assertEqual(response.status_code,200);self.assertIn(b'calculate',response.content)
        self.assertIn('script-src',response['Content-Security-Policy']);self.assertIn("connect-src 'none'",response['Content-Security-Policy'])
        self.assertEqual(response['Cache-Control'],'private, no-store')
        self.assertEqual(_uploaded_editor_items(self.staff)[0].worker_url,'/plugins/zip.calculator/assets/worker.js')
        self.assertEqual(self.client.get('/plugins/zip.calculator/assets/plugin.json').status_code,404)
        self.assertEqual(self.client.get('/plugins/zip.calculator/assets/missing.js').status_code,404)
        self.client.post('/plugins/store/zip.calculator/disable/',HTTP_HX_REQUEST='true')
        self.assertEqual(self.client.get(path).status_code,404)

    def test_asset_auth_error_and_integrity_checks(self):
        self.upload_zip();record=PluginRecord.objects.get(plugin_id='zip.calculator');record.set_enabled(True)
        UserPluginSelection.objects.create(user=self.staff,plugin=record)
        path='/plugins/zip.calculator/assets/worker.js'
        self.client.logout();self.assertEqual(self.client.get(path).status_code,302)
        self.client.force_login(self.staff)
        record.package_manifest['files']['worker.js']='0'*64;record.save()
        self.assertEqual(self.client.get(path).status_code,404)
        record.error='failed';record.save();self.assertEqual(self.client.get(path).status_code,404)

    def test_upload_metadata_conflicts_duplicate_and_invalid_zip_display_errors(self):
        response=self.upload_zip(data={'plugin_id':'different','name':'Different'})
        self.assertEqual(response.status_code,201)
        self.assertEqual(PluginRecord.objects.get(plugin_id="zip.calculator").name,"ZIP Calculator")
        self.assertEqual(self.upload_zip().status_code,400)
        self.assertEqual(self.upload_zip(extra={'../bad.js':b'bad'}).status_code,400)
        self.assertEqual(PluginRecord.objects.filter(source='upload').count(),1)

    def test_manager_also_accepts_zip_upload(self):
        response=self.upload_zip(endpoint='/plugins/upload/')
        self.assertEqual(response.status_code,201);self.assertTrue(PluginRecord.objects.filter(plugin_id='zip.calculator',enabled=False).exists())
        self.assertContains(response,'id="content-container"',count=1,status_code=201)
        self.assertNotContains(response,'<html',status_code=201)

    def test_csrf_and_post_only_store_management(self):
        self.client.auto_csrf=False
        self.assertEqual(self.upload_zip().status_code,403)
        self.assertEqual(self.client.post('/plugins/store/native-test/enable/').status_code,403)
        self.assertIn(self.client.get('/plugins/store/upload/').status_code,(404,405))

    def test_sample_zip_download_is_valid(self):
        response=self.client.get('/plugins/store/example.zip')
        self.assertEqual(response.status_code,200);self.assertIn('attachment',response['Content-Disposition'])
        self.assertEqual(validate_package(response.content)['id'],'demo.zip-calculator')

    def test_package_persistence_failure_cleans_private_storage(self):
        from django.db import IntegrityError
        with patch('apps.plugin_manager.services.PluginRecord.save',side_effect=IntegrityError('collision')):
            self.assertEqual(self.upload_zip().status_code,409)
        self.assertEqual(list((Path(self.folder.name)/'private').rglob('*.zip')),[])
