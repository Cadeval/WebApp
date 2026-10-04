"""X.509 code-signing certificates and CRLs; CA secrets live only in private data."""
import base64
from datetime import timedelta
from hashlib import sha256
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import PluginCertificateAuthority, PluginSigningKey, SigningCertificateRevocation


class CertificateAuthorityUnavailable(ValidationError):
    """Issuance or fresh revocation evidence needs explicit operator repair."""


def freshness():
    now = timezone.now()
    return {'generated_at': now.isoformat(), 'expires_at': (now + timedelta(seconds=300)).isoformat()}


def encode(value): return base64.b64encode(value).decode('ascii')


def certificate(value):
    try:
        if not isinstance(value,str) or len(value)>32768: raise ValueError()
        return x509.load_der_x509_certificate(base64.b64decode(value,validate=True))
    except (ValueError,TypeError) as error: raise ValidationError('Invalid X.509 certificate.') from error


def ca_directory():
    path=Path(getattr(settings,'PLUGIN_CA_DIRECTORY',Path(settings.BASE_DIR)/'data/plugin-ca'))
    if path.is_symlink(): raise ValidationError('CA data directory must not be a symlink.')
    path=path.resolve()
    path.mkdir(mode=0o700,parents=True,exist_ok=True)
    if path.stat().st_mode & 0o077: raise ValidationError('CA data directory must have mode0700.')
    return path


def private_key(authority,kind):
    if not re.fullmatch('[0-9a-f]{64}',authority.fingerprint) or kind not in {'root','issuer','publisher'}: raise ValidationError('Invalid CA private-key identity.')
    path=ca_directory()/(authority.fingerprint+'.'+kind+'.pem')
    try:
        descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(descriptor,'rb') as file:
            info=os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_nlink!=1 or info.st_size>16384: raise ValidationError('Unsafe CA private-key storage.')
            key=serialization.load_pem_private_key(file.read(),password=None)
        expected=ed25519.Ed25519PrivateKey if kind=='publisher' else ec.EllipticCurvePrivateKey
        if not isinstance(key,expected) or (kind!='publisher' and not isinstance(key.curve,ec.SECP256R1)): raise ValidationError('CA private-key algorithm differs from its policy.')
        return key
    except (OSError,ValueError,TypeError) as error: raise CertificateAuthorityUnavailable('The CA is unavailable. Ask an administrator to initialize or restore it.') from error


def _usage(ca):
    return x509.KeyUsage(digital_signature=True,content_commitment=False,key_encipherment=False,data_encipherment=False,key_agreement=False,key_cert_sign=ca,crl_sign=ca,encipher_only=False,decipher_only=False)


def _base(subject,public,issuer,days):
    now=timezone.now()
    return x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(public).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(seconds=60)).not_valid_after(now+timedelta(days=days)).add_extension(x509.SubjectKeyIdentifier.from_public_key(public),False)


def _url(authority,suffix):
    base=getattr(settings,'PLUGIN_CA_PUBLIC_URL','https://cadevil.org').rstrip('/')
    parsed=urlsplit(base)
    if not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or (parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in {'localhost','127.0.0.1','::1'})): raise ValidationError('CA public URL must use HTTPS, or a localhost development origin.')
    return base+'/plugins/trust/'+authority.fingerprint+'/'+suffix


def _leaf(authority,public,label,uris):
    issuer=certificate(authority.intermediate_certificate)
    root=certificate(authority.certificate)
    now=timezone.now()
    if authority.revoked_at or not all(cert.not_valid_before_utc<=now<cert.not_valid_after_utc for cert in [issuer,root]):
        raise CertificateAuthorityUnavailable('The CA has expired or been revoked. Ask an administrator to rotate it.')
    builder=_base(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,label[:64])]),public,issuer.subject,90)
    builder=builder.add_extension(x509.BasicConstraints(ca=False,path_length=None),True).add_extension(_usage(False),True).add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CODE_SIGNING]),True).add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer.public_key()),False).add_extension(x509.SubjectAlternativeName([x509.UniformResourceIdentifier(uri) for uri in uris]),False).add_extension(x509.CRLDistributionPoints([x509.DistributionPoint(full_name=[x509.UniformResourceIdentifier(_url(authority,'leaf.crl'))],relative_name=None,reasons=None,crl_issuer=None)]),False).add_extension(x509.AuthorityInformationAccess([x509.AccessDescription(x509.AuthorityInformationAccessOID.CA_ISSUERS,x509.UniformResourceIdentifier(_url(authority,'intermediate.der')))]),False)
    key = private_key(authority, 'issuer')
    if key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo) != issuer.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo):
        raise CertificateAuthorityUnavailable('The intermediate signing key differs from its certificate.')
    return builder.sign(key,hashes.SHA256())


def initialize_ca(*,rotate=False):
    """Explicit operator action; never called by startup or a web request."""
    if PluginCertificateAuthority.objects.filter(active=True).exists() and not rotate: raise ValidationError('A CA already exists. Use explicit rotation.')
    root_key=ec.generate_private_key(ec.SECP256R1());issuer_key=ec.generate_private_key(ec.SECP256R1());publisher_key=ed25519.Ed25519PrivateKey.generate()
    subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Cadevil plugin root CA')])
    root=_base(subject,root_key.public_key(),subject,3650).add_extension(x509.BasicConstraints(ca=True,path_length=1),True).add_extension(_usage(True),True).add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()),False).sign(root_key,hashes.SHA256())
    root_der=root.public_bytes(serialization.Encoding.DER)
    authority=PluginCertificateAuthority(fingerprint=sha256(root_der).hexdigest(),certificate=encode(root_der))
    issuer_subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Cadevil plugin signing intermediate')])
    issuer=_base(issuer_subject,issuer_key.public_key(),subject,1095).add_extension(x509.BasicConstraints(ca=True,path_length=0),True).add_extension(_usage(True),True).add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()),False).add_extension(x509.CRLDistributionPoints([x509.DistributionPoint(full_name=[x509.UniformResourceIdentifier(_url(authority,'issuer.crl'))],relative_name=None,reasons=None,crl_issuer=None)]),False).add_extension(x509.AuthorityInformationAccess([x509.AccessDescription(x509.AuthorityInformationAccessOID.CA_ISSUERS,x509.UniformResourceIdentifier(_url(authority,'root.der')))]),False).sign(root_key,hashes.SHA256())
    authority.intermediate_certificate=encode(issuer.public_bytes(serialization.Encoding.DER))
    written=[]
    try:
        for kind,key in [('root',root_key),('issuer',issuer_key),('publisher',publisher_key)]:
            path=ca_directory()/(authority.fingerprint+'.'+kind+'.pem')
            descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,getattr(stat,'S_IRUSR',0o400)|getattr(stat,'S_IWUSR',0o200))
            written.append(path)
            with os.fdopen(descriptor,'wb') as file:
                file.write(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));file.flush();os.fsync(file.fileno())
        descriptor=os.open(ca_directory(),os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
        publisher=_leaf(authority,publisher_key.public_key(),'Cadevil bundled browser plugins',['urn:cadevil:publisher:server'])
        authority.publisher_certificate=encode(publisher.public_bytes(serialization.Encoding.DER))
        with transaction.atomic():
            PluginCertificateAuthority.objects.filter(active=True).update(active=False)
            authority.save()
        return authority
    except Exception:
        for path in written: path.unlink(missing_ok=True)
        raise


def current_authority():
    authority=PluginCertificateAuthority.objects.filter(active=True,revoked_at__isnull=True).first()
    if authority is None: raise CertificateAuthorityUnavailable('Plugin signing CA is not initialized. Ask an administrator to run plugin_ca initialize.')
    return authority


def issue_key_certificate(key):
    from .signatures import decode
    if not key.owner_id or not key.owner.is_active or key.revoked_at or (key.team_id and (not key.team.active or not key.team.memberships.filter(user_id=key.owner_id).exists())):
        raise ValidationError('Only active personal keys or keys created by current team members may be certified.')
    authority=current_authority()
    uris=['urn:cadevil:key:'+key.fingerprint,'urn:cadevil:publisher:user:'+str(key.owner_id)]
    if key.team_id: uris.append('urn:cadevil:team:'+str(key.team_id))
    leaf=_leaf(authority,ed25519.Ed25519PublicKey.from_public_bytes(decode(key.public_key,32)),key.label,uris)
    key.certificate=encode(leaf.public_bytes(serialization.Encoding.DER));key.certificate_serial=format(leaf.serial_number,'x');key.certificate_authority=authority
    key.save(update_fields=['certificate','certificate_serial','certificate_authority'])
    return key


def renew_publisher_certificate():
    """Explicit operator renewal; the durable publisher private key is retained."""
    authority=current_authority()
    with transaction.atomic():
        authority=PluginCertificateAuthority.objects.select_for_update().get(pk=authority.pk)
        if authority.publisher_certificate:
            previous=certificate(authority.publisher_certificate)
            SigningCertificateRevocation.objects.get_or_create(authority=authority,serial=format(previous.serial_number,'x'),defaults={'reason':'Bundled publisher certificate renewed'})
        publisher=_leaf(authority,private_key(authority,'publisher').public_key(),'Cadevil bundled browser plugins',['urn:cadevil:publisher:server'])
        authority.publisher_certificate=encode(publisher.public_bytes(serialization.Encoding.DER))
        authority.save(update_fields=['publisher_certificate'])
    return authority


def certificate_chain(key):
    authority=key.certificate_authority
    if not authority or not key.certificate: return []
    return [key.certificate,authority.intermediate_certificate,authority.certificate]


def validate_chain(chain,authority,*,public_key=None):
    if not isinstance(chain,list) or len(chain)!=3 or authority is None or authority.revoked_at: raise ValidationError('The certificate authority is unknown or revoked.')
    leaf,issuer,root=map(certificate,chain)
    try:
        if chain[1:]!=[authority.intermediate_certificate,authority.certificate] or sha256(root.public_bytes(serialization.Encoding.DER)).hexdigest()!=authority.fingerprint: raise ValueError()
        for cert in [root,issuer,leaf]:
            if not cert.not_valid_before_utc<=timezone.now()<cert.not_valid_after_utc: raise ValueError()
            for extension in cert.extensions:
                if extension.critical and not isinstance(extension.value,(x509.BasicConstraints,x509.KeyUsage,x509.ExtendedKeyUsage)): raise ValueError()
        root.verify_directly_issued_by(root);issuer.verify_directly_issued_by(root);leaf.verify_directly_issued_by(issuer)
        for cert,length in [(root,1),(issuer,0)]:
            if not isinstance(cert.public_key(), ec.EllipticCurvePublicKey) or not isinstance(cert.public_key().curve, ec.SECP256R1) or cert.signature_hash_algorithm.name != 'sha256': raise ValueError()
            constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints)
            if not constraints.critical or constraints.value!=x509.BasicConstraints(ca=True,path_length=length): raise ValueError()
            extension = cert.extensions.get_extension_for_class(x509.KeyUsage)
            if not extension.critical: raise ValueError()
            usage=extension.value
            if usage!=_usage(True): raise ValueError()
        constraints = leaf.extensions.get_extension_for_class(x509.BasicConstraints)
        if not constraints.critical or constraints.value.ca or leaf.signature_hash_algorithm.name != 'sha256': raise ValueError()
        extension = leaf.extensions.get_extension_for_class(x509.KeyUsage)
        if not extension.critical: raise ValueError()
        usage=extension.value
        if usage!=_usage(False): raise ValueError()
        eku=leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
        if not eku.critical or list(eku.value)!=[ExtendedKeyUsageOID.CODE_SIGNING]: raise ValueError()
        if not isinstance(leaf.public_key(),ed25519.Ed25519PublicKey): raise ValueError()
        if public_key is not None and leaf.public_key().public_bytes_raw()!=public_key: raise ValueError()
        if authority.revocations.filter(serial=format(leaf.serial_number,'x')).exists(): raise ValueError()
        return leaf
    except (ValueError,TypeError,InvalidSignature,x509.ExtensionNotFound,x509.DuplicateExtension) as error: raise ValidationError('The signing certificate is invalid, expired or revoked.') from error


def validate_signing_key(key):
    from .signatures import decode
    if not key or key.revoked_at or not key.owner_id or not key.owner.is_active or (key.team_id and not key.team.active): raise ValidationError('The signing key is inactive or revoked.')
    public=decode(key.public_key,32)
    if sha256(public).hexdigest()!=key.fingerprint: raise ValidationError('Signing key fingerprint differs from its public key.')
    if key.team_id and not key.team.memberships.filter(user_id=key.owner_id).exists(): raise ValidationError('The key creator no longer belongs to its team.')
    leaf=validate_chain(certificate_chain(key),key.certificate_authority,public_key=public)
    names=leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.UniformResourceIdentifier)
    required={'urn:cadevil:key:'+key.fingerprint,'urn:cadevil:publisher:user:'+str(key.owner_id)}
    if key.team_id: required.add('urn:cadevil:team:'+str(key.team_id))
    if set(names)!=required or key.certificate_serial!=format(leaf.serial_number,'x'): raise ValidationError('Signing certificate identity differs from its registered key.')
    return leaf


def trusted_key(key):
    try: validate_signing_key(key);return True
    except (ValidationError,AttributeError,ValueError): return False


def revoke_key(key,*,reason='Signing key revoked'):
    with transaction.atomic():
        key=PluginSigningKey.objects.select_for_update().get(pk=key.pk)
        if key.certificate_authority_id and key.certificate_serial:
            SigningCertificateRevocation.objects.get_or_create(authority=key.certificate_authority,serial=key.certificate_serial,defaults={'reason':reason[:120]})
        if not key.revoked_at:
            key.revoked_at=timezone.now();key.save(update_fields=['revoked_at'])
        key.plugins.update(enabled=False,error='The package signing key or certificate was revoked.')


def revoke_authority(authority):
    with transaction.atomic():
        authority=PluginCertificateAuthority.objects.select_for_update().get(pk=authority.pk)
        authority.revoked_at=timezone.now();authority.active=False;authority.save(update_fields=['revoked_at','active'])
        for key in authority.signing_keys.filter(revoked_at__isnull=True): revoke_key(key,reason='Certificate authority revoked')


def crl(authority,*,issuer=False):
    cert=certificate(authority.certificate if issuer else authority.intermediate_certificate)
    now=timezone.now()
    builder=x509.CertificateRevocationListBuilder().issuer_name(cert.subject).last_update(now-timedelta(seconds=5)).next_update(now+timedelta(seconds=300)).add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(cert.public_key()),False)
    revoked=[(format(certificate(authority.intermediate_certificate).serial_number,'x'),authority.revoked_at)] if issuer and authority.revoked_at else ([] if issuer else list(authority.revocations.values_list('serial','revoked_at')))
    held=set()
    if not issuer:
        # Account administration can deactivate a signer or remove a membership
        # outside the team page. Fresh CRLs must reflect that loss of authority
        # without a request mutating approvals or migration state.
        from .models import TeamMembership
        keys=list(authority.signing_keys.select_related('owner','team').exclude(certificate_serial=''))
        memberships=set(TeamMembership.objects.filter(team_id__in={key.team_id for key in keys if key.team_id},user_id__in={key.owner_id for key in keys if key.owner_id}).values_list('team_id','user_id'))
        serials={serial for serial,_ in revoked}
        for key in keys:
            inactive=key.revoked_at or not key.owner_id or not key.owner.is_active or (key.team_id and (not key.team.active or (key.team_id,key.owner_id) not in memberships))
            if inactive and key.certificate_serial not in serials:
                revoked.append((key.certificate_serial,key.revoked_at or now))
                serials.add(key.certificate_serial)
                if not key.revoked_at: held.add(key.certificate_serial)
    for serial,when in revoked:
        entry=x509.RevokedCertificateBuilder().serial_number(int(serial,16)).revocation_date(when).add_extension(x509.CRLReason(x509.ReasonFlags.certificate_hold if serial in held else x509.ReasonFlags.unspecified),False).build()
        builder=builder.add_revoked_certificate(entry)
    key = private_key(authority,'root' if issuer else 'issuer')
    if key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo) != cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo):
        raise CertificateAuthorityUnavailable('The CRL signing key differs from its certificate.')
    return builder.sign(key,hashes.SHA256()).public_bytes(serialization.Encoding.DER)


def trust_bundle(key):
    validate_signing_key(key)
    authority=key.certificate_authority
    return {'format':'cadevil-plugin-trust-x509-v1',**freshness(),'certificate_chain':certificate_chain(key),'leaf_crl':encode(crl(authority)),'issuer_crl':encode(crl(authority,issuer=True))}


def bundled_trust(plugin_id,files):
    from .signatures import canonical_payload
    authority=current_authority()
    chain=[authority.publisher_certificate,authority.intermediate_certificate,authority.certificate]
    publisher=validate_chain(chain,authority)
    if publisher.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.UniformResourceIdentifier) != ['urn:cadevil:publisher:server']:
        raise ValidationError('The bundled publisher certificate has an invalid scope.')
    key=private_key(authority,'publisher')
    if publisher.public_key().public_bytes_raw()!=key.public_key().public_bytes_raw(): raise ValidationError('Bundled publisher key differs from its certificate.')
    hashes_by_name={name:sha256(content).hexdigest() for name,content in files.items()}
    return {'format':'cadevil-plugin-trust-x509-v1',**freshness(),'certificate_chain':chain,'leaf_crl':encode(crl(authority)),'issuer_crl':encode(crl(authority,issuer=True)),'files':hashes_by_name,'signature':{'format':'cadevil-plugin-signature-v1','algorithm':'Ed25519','key_id':sha256(key.public_key().public_bytes_raw()).hexdigest(),'signature':encode(key.sign(canonical_payload(hashes_by_name)))},'plugin_id':plugin_id}
