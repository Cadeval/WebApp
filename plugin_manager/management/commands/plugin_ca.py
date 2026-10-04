"""Explicit CA initialization, rotation, backfill and revocation."""
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from plugin_manager.certificate_authority import initialize_ca, issue_key_certificate, revoke_authority, renew_publisher_certificate
from plugin_manager.models import PluginCertificateAuthority, PluginSigningKey


class Command(BaseCommand):
    help='Manage the private X.509 plugin-signing CA under the durable data directory.'

    def add_arguments(self,parser):
        parser.add_argument('action',choices=['initialize','rotate','backfill','renew-publisher','revoke'])
        parser.add_argument('--root-id')

    def handle(self,*args,**options):
        try:
            action=options['action']
            if action in {'initialize','rotate'}:
                ca=initialize_ca(rotate=action=='rotate')
                self.stdout.write('CA root initialized: '+ca.fingerprint)
            elif action=='backfill':
                count=0
                for key in PluginSigningKey.objects.filter(revoked_at__isnull=True,owner__is_active=True):
                    if not key.certificate:
                        if key.team_id and (not key.team.active or not key.team.memberships.filter(user_id=key.owner_id).exists()): continue
                        issue_key_certificate(key);count+=1
                self.stdout.write(f'Certified {count} existing public keys; no user private keys were accessed.')
            elif action=='renew-publisher':
                ca=renew_publisher_certificate()
                self.stdout.write('Bundled publisher certificate renewed for root '+ca.fingerprint)
            else:
                ca=PluginCertificateAuthority.objects.get(fingerprint=options.get('root_id'))
                revoke_authority(ca);self.stdout.write('CA root revoked; affected uploaded plugins disabled.')
        except (ValidationError,PluginCertificateAuthority.DoesNotExist) as error:
            raise CommandError(str(error)) from error
