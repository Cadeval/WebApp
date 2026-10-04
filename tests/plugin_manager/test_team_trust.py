"""Team authorization and standard X.509 trust enforce the same publication boundary."""
import base64
from datetime import timedelta
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from plugin_manager import certificate_authority as ca
from plugin_manager.models import (PluginRecord, PluginSigningKey, Team, TeamMembership,
                                   PluginCertificateAuthority, SigningCertificateRevocation, UserPluginSelection)
from plugin_manager.signatures import canonical_payload, registration_payload
from plugin_manager.teams import can_publish_key, can_manage_team
from tests.bolt_browser import BoltBrowser
from django_bolt import BoltAPI
from config.api import api as main_api
from mycelium.api import api as home_api
from plugin_manager.api import api as plugin_api


class TeamTrustTests(TestCase):
    def setUp(self):
        users=get_user_model()
        self.manager=users.objects.create_user(username='team-manager')
        self.member=users.objects.create_user(username='team-member')
        self.outsider=users.objects.create_user(username='outside-team')
        self.manager.user_permissions.add(*Permission.objects.filter(content_type__app_label='plugin_manager',codename__in=['add_team','change_team']))
        self.team=Team.objects.create(name='Building team',created_by=self.manager)
        TeamMembership.objects.create(team=self.team,user=self.manager,role='manager')
        TeamMembership.objects.create(team=self.team,user=self.member)
        self.authority=ca.initialize_ca()
        self.private=ed25519.Ed25519PrivateKey.generate()
        public=self.private.public_key().public_bytes_raw()
        self.key=PluginSigningKey.objects.create(owner=self.manager,team=self.team,label='Team publisher',fingerprint=hashlib.sha256(public).hexdigest(),public_key=base64.b64encode(public).decode())
        ca.issue_key_certificate(self.key)
        combined=BoltAPI(trailing_slash='keep',django_middleware=True)
        for child in [main_api,home_api,plugin_api]: combined.mount('',child)
        self.client=BoltBrowser(api=combined)
        self.addCleanup(self.client.close)
        self.login(self.manager)
        self.client.get('/mycelium/settings/teams')

    def login(self,user):
        self.client.logout()
        self.client.force_login(user)

    def test_certificates_use_standard_ca_constraints_and_code_signing(self):
        leaf,issuer,root=map(ca.certificate,ca.certificate_chain(self.key))
        self.assertIsInstance(root.public_key().curve,ec.SECP256R1)
        self.assertIsInstance(leaf.public_key(),ed25519.Ed25519PublicKey)
        root.verify_directly_issued_by(root);issuer.verify_directly_issued_by(root);leaf.verify_directly_issued_by(issuer)
        self.assertEqual(root.extensions.get_extension_for_class(x509.BasicConstraints).value.path_length,1)
        self.assertEqual(issuer.extensions.get_extension_for_class(x509.BasicConstraints).value.path_length,0)
        self.assertEqual(list(leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value),[ExtendedKeyUsageOID.CODE_SIGNING])
        self.assertTrue(ca.trusted_key(self.key))

    def test_fresh_standard_crls_verify_and_include_revoked_serial(self):
        ca.revoke_key(self.key)
        for issuer_flag,certificate_value in [(False,self.authority.intermediate_certificate),(True,self.authority.certificate)]:
            parsed=x509.load_der_x509_crl(ca.crl(self.authority,issuer=issuer_flag))
            self.assertTrue(parsed.is_signature_valid(ca.certificate(certificate_value).public_key()))
            self.assertLessEqual((parsed.next_update_utc-parsed.last_update_utc).total_seconds(),310)
            self.assertLess(parsed.last_update_utc,timezone.now())
            if not issuer_flag: self.assertIsNotNone(parsed.get_revoked_certificate_by_serial_number(int(self.key.certificate_serial,16)))

    def test_ca_private_files_are_private_and_user_private_key_is_absent(self):
        paths=list(ca.ca_directory().glob(self.authority.fingerprint+'.*.pem'))
        self.assertEqual(len(paths),3)
        self.assertTrue(all(path.stat().st_mode&0o077==0 for path in paths))
        self.assertNotIn(self.private.private_bytes_raw(),b''.join(path.read_bytes() for path in paths))
        self.assertFalse(any('private' in field.name for field in self.key._meta.fields))

    def test_member_reads_public_team_key_but_cannot_manage_it(self):
        self.login(self.member)
        response=self.client.get(f'/plugins/teams/{self.team.pk}/keys.json')
        self.assertEqual(response.status_code,200,response.content)
        data=response.json()['keys'][0]
        self.assertEqual(data['scope'],'team');self.assertEqual(data['team_id'],str(self.team.pk))
        self.assertEqual(data['public_key'],self.key.public_key)
        self.assertNotIn('encrypted_private_key',data)
        self.assertEqual(self.client.post(f'/plugins/keys/{self.key.fingerprint}/revoke/').status_code,403)
        self.assertTrue(can_publish_key(self.member,self.key))
        self.assertFalse(can_manage_team(self.member,self.team))
        for encoding in ['der','pem']:
            response=self.client.get(f'/plugins/keys/{self.key.fingerprint}/certificate.{encoding}')
            self.assertEqual(response.status_code,200,response.content)
            parsed=x509.load_der_x509_certificate(response.content) if encoding=='der' else x509.load_pem_x509_certificate(response.content)
            self.assertEqual(parsed.public_key().public_bytes_raw(),self.private.public_key().public_bytes_raw())
        root=self.client.get(f'/plugins/trust/{self.authority.fingerprint}/root.pem')
        self.assertEqual(root.status_code,200,root.content)
        self.assertEqual(x509.load_pem_x509_certificate(root.content).fingerprint(hashes.SHA256()).hex(),self.authority.fingerprint)

    def test_outsider_cannot_read_team_keys_or_publish(self):
        self.login(self.outsider)
        self.assertEqual(self.client.get(f'/plugins/teams/{self.team.pk}/keys.json').status_code,403)
        self.assertEqual(self.client.get(f'/plugins/keys/{self.key.fingerprint}/public.json').status_code,403)
        self.assertFalse(can_publish_key(self.outsider,self.key))
        self.assertEqual(self.client.get('/plugins/teams/not-a-uuid/keys.json').status_code,404)

    def test_manager_role_needs_django_permission_and_grants_no_group_privileges(self):
        groups=list(self.member.groups.values_list('pk',flat=True))
        TeamMembership.objects.filter(team=self.team,user=self.member).update(role='manager')
        self.assertFalse(can_manage_team(self.member,self.team))
        self.assertFalse(self.member.is_staff)
        self.assertEqual(list(self.member.groups.values_list('pk',flat=True)),groups)
        self.login(self.member)
        self.assertEqual(self.client.post('/mycelium/settings/teams',{'action':'rename','team_id':str(self.team.pk),'name':'Denied'}).status_code,403)

    def test_team_creation_requires_permission_and_full_htmx_use_one_content(self):
        self.login(self.outsider)
        self.assertEqual(self.client.post('/mycelium/settings/teams',{'action':'create','name':'Denied'}).status_code,403)
        self.login(self.manager)
        response=self.client.post('/mycelium/settings/teams',{'action':'create','name':'Second team'},HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code,200,response.content)
        self.assertContains(response,'id="content-container"',count=1)
        self.assertNotContains(response,'<html')
        team=Team.objects.get(name='Second team')
        self.assertEqual(team.memberships.get(user=self.manager).role,'manager')
        self.assertContains(self.client.get('/mycelium/settings/teams'),'<html')

    def test_removal_revokes_created_team_keys_and_blocks_public_read(self):
        self.key.owner=self.member;self.key.save(update_fields=['owner']);ca.issue_key_certificate(self.key)
        record=PluginRecord.objects.create(plugin_id='team.upload',source='upload',artifact_type='zip',signing_key=self.key,enabled=True)
        response=self.client.post('/mycelium/settings/teams',{'action':'remove','team_id':str(self.team.pk),'username':self.member.username})
        self.assertEqual(response.status_code,200,response.content)
        self.key.refresh_from_db();record.refresh_from_db()
        self.assertIsNotNone(self.key.revoked_at);self.assertFalse(record.enabled)
        self.assertFalse(ca.trusted_key(self.key))
        self.login(self.member)
        self.assertEqual(self.client.get(f'/plugins/teams/{self.team.pk}/keys.json').status_code,403)

    def test_last_manager_cannot_be_removed_or_demoted(self):
        for action in ['remove','role']:
            response=self.client.post('/mycelium/settings/teams',{'action':action,'team_id':str(self.team.pk),'username':self.manager.username,'role':'member'})
            self.assertEqual(response.status_code,400)
        self.assertEqual(self.team.memberships.get(user=self.manager).role,'manager')

    def test_expiry_tampering_and_root_revocation_fail_closed(self):
        with patch('plugin_manager.certificate_authority.timezone.now',return_value=timezone.now()+timedelta(days=91)):
            self.assertFalse(ca.trusted_key(self.key))
        original=self.key.fingerprint
        self.key.fingerprint='0'*64;self.assertFalse(ca.trusted_key(self.key));self.key.fingerprint=original
        ca.revoke_authority(self.authority)
        self.authority.refresh_from_db()
        self.key.refresh_from_db();self.assertFalse(ca.trusted_key(self.key))
        self.assertFalse(self.client.get('/plugins/trust/roots.json').json()['roots'])
        root_crl=x509.load_der_x509_crl(ca.crl(self.authority,issuer=True))
        serial=ca.certificate(self.authority.intermediate_certificate).serial_number
        self.assertIsNotNone(root_crl.get_revoked_certificate_by_serial_number(serial))

    def test_rotation_keeps_old_root_trusted_until_explicit_revocation(self):
        new=ca.initialize_ca(rotate=True)
        self.assertNotEqual(new.pk,self.authority.pk)
        self.assertTrue(ca.trusted_key(self.key))
        self.assertEqual(len(self.client.get('/plugins/trust/roots.json').json()['roots']),2)

    def test_pending_ca_denies_execution_without_erasing_approval_or_selection(self):
        from plugin_manager.registry import PluginRegistry
        from plugin_manager.workflows import selectable_plugin
        record=PluginRecord.objects.create(plugin_id='old.upload',source='upload',artifact_type='zip',artifact='private/old.zip',signing_key=self.key,enabled=True,version='1.0.0')
        record.observe_version();record.save()
        UserPluginSelection.objects.create(plugin=record,user=self.manager)
        PluginSigningKey.objects.filter(pk=self.key.pk).update(certificate='',certificate_serial='',certificate_authority=None)
        PluginRegistry().sync_plugin_records([])
        record.refresh_from_db()
        self.assertTrue(record.enabled)
        self.assertEqual(len(record.version_history),1)
        self.assertTrue(record.user_selections.filter(user=self.manager).exists())
        self.assertFalse(selectable_plugin(record))

    def test_bundled_publisher_has_separate_leaf_and_signs_exact_file_map(self):
        data=ca.bundled_trust('exampleeditor',{'worker.js':b'worker','module.wasm':b'wasm'})
        leaf=ca.certificate(data['certificate_chain'][0])
        leaf.public_key().verify(base64.b64decode(data['signature']['signature']),canonical_payload(data['files']))
        self.assertNotEqual(data['signature']['key_id'],self.key.fingerprint)
        self.assertEqual(len(data['certificate_chain']),3)
        self.assertNotIn('private',','.join(data))
        self.assertLessEqual((timezone.datetime.fromisoformat(data['expires_at'])-timezone.datetime.fromisoformat(data['generated_at'])).total_seconds(),300)

    def test_missing_ca_does_not_bootstrap_or_leave_registered_key(self):
        PluginCertificateAuthority.objects.update(active=False)
        public=ed25519.Ed25519PrivateKey.generate()
        context=self.client.get('/mycelium/settings',{'section':'security'}).context['registration']
        raw=base64.b64encode(public.public_key().public_bytes_raw()).decode()
        payload={'label':'Unavailable CA','public_key':raw,'challenge':context['challenge'],'proof':base64.b64encode(public.sign(registration_payload(context['challenge'],self.manager.pk,raw))).decode()}
        before=PluginSigningKey.objects.count()
        response=self.client.request('POST','/plugins/keys/register/',content=json.dumps(payload).encode(),content_type='application/json')
        self.assertEqual(response.status_code,503,response.content)
        self.assertEqual(PluginSigningKey.objects.count(),before)
        self.assertEqual(PluginCertificateAuthority.objects.count(),1)

    def test_team_registration_proof_is_bound_to_exact_team(self):
        private=ed25519.Ed25519PrivateKey.generate()
        context=self.client.get('/mycelium/settings',{'section':'security'}).context['registration']
        raw=base64.b64encode(private.public_key().public_bytes_raw()).decode()
        payload={'label':'New team key','team_id':str(self.team.pk),'public_key':raw,'challenge':context['challenge'],'proof':base64.b64encode(private.sign(registration_payload(context['challenge'],self.manager.pk,raw,team_id=str(self.team.pk)))).decode()}
        response=self.client.request('POST','/plugins/keys/register/',content=json.dumps(payload).encode(),content_type='application/json')
        self.assertEqual(response.status_code,201,response.content)
        self.assertEqual(response.json()['scope'],'team')
        self.assertTrue(ca.trusted_key(PluginSigningKey.objects.get(fingerprint=response.json()['key_id'])))

    def test_renewal_revokes_previous_certificate_without_exporting_private_key(self):
        old=self.key.certificate_serial
        response=self.client.post(f'/plugins/keys/{self.key.fingerprint}/renew/')
        self.assertEqual(response.status_code,200,response.content)
        self.key.refresh_from_db()
        self.assertNotEqual(old,self.key.certificate_serial)
        self.assertTrue(SigningCertificateRevocation.objects.filter(authority=self.authority,serial=old).exists())
        self.assertTrue(ca.trusted_key(self.key))

    def test_ca_revocation_requires_explicit_admin_permission(self):
        self.assertEqual(self.client.post(f'/plugins/trust/{self.authority.fingerprint}/revoke/').status_code,403)
        self.manager.is_staff=True;self.manager.save(update_fields=['is_staff'])
        self.manager.user_permissions.add(Permission.objects.get(codename='change_plugincertificateauthority'))
        response=self.client.post(f'/plugins/trust/{self.authority.fingerprint}/revoke/')
        self.assertEqual(response.status_code,200,response.content)

    def test_member_can_publish_team_signature_and_loses_publication_after_removal(self):
        def package(plugin_id):
            files={'plugin.json':json.dumps({'id':plugin_id,'name':'Team tool','version':'1.0.0','api_version':'1.0','type':'javascript','entrypoint':'worker.js'}).encode(),'worker.js':b'self.onmessage=()=>postMessage({type:"ready"});'}
            signature={'format':'cadevil-plugin-signature-v1','algorithm':'Ed25519','key_id':self.key.fingerprint,'signature':base64.b64encode(self.private.sign(canonical_payload({name:hashlib.sha256(value).hexdigest() for name,value in files.items()}))).decode(),'certificate_chain':ca.certificate_chain(self.key)}
            memory=io.BytesIO()
            with ZipFile(memory,'w') as archive:
                for name,value in files.items(): archive.writestr(name,value)
                archive.writestr('signature.json',json.dumps(signature))
            return memory.getvalue()
        with TemporaryDirectory() as directory,override_settings(PLUGIN_ARTIFACT_ROOT=Path(directory)/'private'):
            self.login(self.member)
            self.client.get('/mycelium/settings',{'section':'security'})
            response=self.client.post('/plugins/store/upload/',{'artifact':SimpleUploadedFile('team.zip',package('team-first'))},HTTP_HX_REQUEST='true')
            self.assertEqual(response.status_code,201,response.content)
            record=PluginRecord.objects.get(plugin_id='team-first')
            self.assertEqual(record.uploaded_by,self.member);self.assertEqual(record.signing_key,self.key)
            self.assertFalse(record.enabled)
            self.login(self.manager)
            self.assertTrue(can_manage_team(self.manager,self.team))
            account=self.client.get('/mycelium/settings',{'section':'security'})
            self.assertEqual(account.context['user'].pk,self.manager.pk)
            removed=self.client.post('/mycelium/settings/teams',{'action':'remove','team_id':str(self.team.pk),'username':self.member.username})
            self.assertEqual(removed.status_code,200,removed.content)
            self.assertFalse(TeamMembership.objects.filter(team=self.team,user=self.member).exists())
            self.login(self.member)
            self.client.get('/mycelium/settings',{'section':'security'})
            response=self.client.post('/plugins/store/upload/',{'artifact':SimpleUploadedFile('team.zip',package('team-second'))})
            self.assertEqual(response.status_code,403,response.content)
            self.assertFalse(PluginRecord.objects.filter(plugin_id='team-second').exists())

    def test_cli_accepts_public_renewal_bundle_and_rejects_expired_certificate(self):
        from plugin_manager.sign_cli import sign_package
        from .test_signing import encrypted_key
        from .test_store import package
        password='local-test-passphrase'
        with TemporaryDirectory() as directory:
            directory=Path(directory)
            key=directory/'encrypted-key.json';key.write_text(json.dumps(encrypted_key(self.private,password)))
            public=directory/'renewed-certificate.json';public.write_text(json.dumps({'certificate_chain':ca.certificate_chain(self.key)}))
            source=directory/'plugin.zip';source.write_bytes(package())
            output=directory/'signed.zip'
            sign_package(source,key,output,password,public)
            with ZipFile(output) as archive: signature=json.loads(archive.read('signature.json'))
            self.assertEqual(signature['certificate_chain'],ca.certificate_chain(self.key))
            self.assertNotIn('encrypted_private_key',signature)
            leaf=ca.certificate(self.key.certificate)
            now=timezone.now()
            expired=x509.CertificateBuilder().subject_name(leaf.subject).issuer_name(leaf.issuer).public_key(leaf.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=2)).not_valid_after(now-timedelta(days=1))
            for extension in leaf.extensions: expired=expired.add_extension(extension.value,extension.critical)
            expired=expired.sign(ca.private_key(self.authority,'issuer'),hashes.SHA256())
            chain=ca.certificate_chain(self.key);chain[0]=ca.encode(expired.public_bytes(serialization.Encoding.DER))
            public.write_text(json.dumps({'certificate_chain':chain}))
            rejected=directory/'rejected.zip'
            with self.assertRaises(ValueError): sign_package(source,key,rejected,password,public)
            self.assertFalse(rejected.exists())

    def test_missing_or_unsafe_ca_secret_fails_closed(self):
        publisher=ca.ca_directory()/(self.authority.fingerprint+'.publisher.pem')
        publisher.chmod(0o644)
        with self.assertRaises(ValidationError): ca.bundled_trust('exampleeditor',{'worker.js':b'worker'})
        publisher.chmod(0o600)
        publisher.unlink()
        with self.assertRaises(ValidationError): ca.bundled_trust('exampleeditor',{'worker.js':b'worker'})

    def test_fresh_crl_reflects_account_deactivation_outside_team_ui(self):
        get_user_model().objects.filter(pk=self.manager.pk).update(is_active=False)
        parsed=x509.load_der_x509_crl(ca.crl(self.authority))
        revoked=parsed.get_revoked_certificate_by_serial_number(int(self.key.certificate_serial,16))
        self.assertIsNotNone(revoked)
        self.assertEqual(revoked.extensions.get_extension_for_class(x509.CRLReason).value.reason,x509.ReasonFlags.certificate_hold)
        self.key.refresh_from_db();self.assertFalse(ca.trusted_key(self.key))
        get_user_model().objects.filter(pk=self.manager.pk).update(is_active=True)
        self.assertIsNone(x509.load_der_x509_crl(ca.crl(self.authority)).get_revoked_certificate_by_serial_number(int(self.key.certificate_serial,16)))
