/** X.509/CRL parsing and path building use the maintained PKIjs implementation. */
import {Certificate, CertificateRevocationList, CertificateChainValidationEngine, CryptoEngine, setEngine} from './vendor/browser_pki.js';

const SHA256_ECDSA = '1.2.840.10045.4.3.2';
const CODE_SIGNING = '1.3.6.1.5.5.7.3.3';
const MAX_DOCUMENT_BYTES = 128 * 1024;
const SERVER_BUNDLES = Object.freeze(['cadevil.example.editor','cadevil.rust-example.editor','cadevil.browser.wasm-wrapper']);
export function isBundledPluginId(pluginId) {return SERVER_BUNDLES.includes(pluginId);}
const encoder = new TextEncoder();
export function base64Bytes(value, maximum = MAX_DOCUMENT_BYTES) {
    if (typeof value !== 'string' || !value || value.length > Math.ceil(maximum / 3) * 4 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) throw Error('Invalid trust encoding.');
    const binary = atob(value);
    const bytes = Uint8Array.from(binary, character => character.charCodeAt(0));
    if (bytes.length > maximum || btoa(binary) !== value) throw Error('Invalid trust encoding.');
    return bytes;
}
export async function sha256(bytes, crypto = globalThis.crypto) {
    if (!crypto?.subtle) throw Error('This browser cannot verify plugin signatures.');
    return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), value => value.toString(16).padStart(2,'0')).join('');
}
function exact(value, keys) {return value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value,key));}
export function canonicalPackage(files) {
    if (!files || Array.isArray(files) || typeof files !== 'object' || !Object.keys(files).length || Object.keys(files).length > 32) throw Error('Invalid signed file inventory.');
    const names = Object.keys(files).sort();
    for (const name of names) {
        if (!/^[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*$/.test(name) || name.split('/').some(part => part === '.' || part === '..') || !/^[a-f0-9]{64}$/.test(files[name])) throw Error('Invalid signed file inventory.');
    }
    return encoder.encode('{"context":"cadevil-plugin-package-v1","files":{'+names.map(name => JSON.stringify(name)+':'+JSON.stringify(files[name])).join(',')+'}}');
}
function extension(certificate, oid) {
    const matches = (certificate.extensions || []).filter(value => value.extnID === oid);
    if (matches.length !== 1 || !matches[0].parsedValue) throw Error('Signing certificate has a missing or duplicate extension.');
    return matches[0];
}
function certificatePolicy(certificate, ca, pathLength, now) {
    if (certificate.signatureAlgorithm.algorithmId !== SHA256_ECDSA || certificate.signature.algorithmId !== SHA256_ECDSA
        || certificate.notBefore.value > now || certificate.notAfter.value <= now) throw Error('Signing certificate is expired or uses an unsupported issuer algorithm.');
    const constraints = extension(certificate,'2.5.29.19');
    const usage = extension(certificate,'2.5.29.15');
    if (!constraints.critical || !usage.critical || Boolean(constraints.parsedValue.cA) !== ca) throw Error('Invalid signing certificate constraints.');
    const bits = usage.parsedValue.valueBlock.valueHexView;
    if (bits.length !== 1 || !(bits[0] & 0x80) || (ca ? bits[0] !== 0x86 : bits[0] !== 0x80)) throw Error('Invalid signing certificate key usage.');
    if (ca) {
        if (constraints.parsedValue.pathLenConstraint !== pathLength
            || certificate.subjectPublicKeyInfo.algorithm.algorithmId !== '1.2.840.10045.2.1'
            || certificate.subjectPublicKeyInfo.algorithm.algorithmParams?.valueBlock?.toString() !== '1.2.840.10045.3.1.7') throw Error('Invalid signing authority constraints.');
    } else {
        const eku = extension(certificate,'2.5.29.37');
        if (!eku.critical || eku.parsedValue.keyPurposes.length !== 1 || eku.parsedValue.keyPurposes[0] !== CODE_SIGNING
            || certificate.subjectPublicKeyInfo.algorithm.algorithmId !== '1.3.101.112'
            || certificate.subjectPublicKeyInfo.algorithm.algorithmParams !== undefined) throw Error('Certificate is not an Ed25519 code-signing certificate.');
    }
    const allowedCritical = new Set(['2.5.29.19','2.5.29.15','2.5.29.37']);
    if ((certificate.extensions || []).some(value => value.critical && !allowedCritical.has(value.extnID))) throw Error('Unsupported critical signing certificate extension.');
}
function publisherPolicy(leaf,document) {
    const names = extension(leaf,'2.5.29.17').parsedValue.altNames;
    if (!Array.isArray(names) || names.some(name => name.type !== 6 || typeof name.value !== 'string')) throw Error('Invalid signing certificate publisher scope.');
    const uris=names.map(name => name.value);
    if (new Set(uris).size !== uris.length) throw Error('Invalid signing certificate publisher scope.');
    if (isBundledPluginId(document.plugin_id)) {
        if (uris.length !== 1 || uris[0] !== 'urn:cadevil:publisher:server') throw Error('Bundled plugins require the server publisher certificate.');
    } else {
        if (![2,3].includes(uris.length) || !uris.includes('urn:cadevil:key:'+document.signature.key_id)
            || uris.filter(uri => /^urn:cadevil:publisher:user:[1-9][0-9]*$/.test(uri)).length !== 1
            || (uris.length === 3 && uris.filter(uri => /^urn:cadevil:team:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(uri)).length !== 1)) throw Error('Uploaded plugins require a scoped user or team publisher certificate.');
    }
}
function freshness(document, format, now) {
    if (document?.format !== format) throw Error('Unknown plugin trust format.');
    const generated = Date.parse(document.generated_at), expires = Date.parse(document.expires_at);
    if (!Number.isFinite(generated) || !Number.isFinite(expires) || generated > +now + 30000 || generated < +now - 330000
        || expires <= +now || expires > generated + 330000) throw Error('Plugin trust data is stale.');
}
async function verifyCRL(encoded, issuer, certificate, now, cryptoEngine) {
    const crl = CertificateRevocationList.fromBER(base64Bytes(encoded).buffer);
    const from = crl.thisUpdate.value, until = crl.nextUpdate?.value;
    if (!until || from > now || +from < +now - 330000 || until <= now || +until - +from > 330000
        || !crl.issuer.isEqual(issuer.subject) || crl.signatureAlgorithm.algorithmId !== SHA256_ECDSA
        || crl.signature.algorithmId !== SHA256_ECDSA || (crl.crlExtensions?.extensions || []).some(value => value.critical)) throw Error('Plugin revocation data is stale or invalid.');
    if (!(await crl.verify({issuerCertificate:issuer},cryptoEngine)) || crl.isCertificateRevoked(certificate)) throw Error('The plugin signing certificate was revoked or its revocation list is invalid.');
    return crl;
}
export async function verifyPluginTrust(document, roots, {crypto = globalThis.crypto, now = new Date(), expectedPluginId} = {}) {
    if (!crypto?.subtle || !(now instanceof Date) || !Number.isFinite(+now)) throw Error('This browser cannot verify plugin certificates.');
    freshness(document,'cadevil-plugin-trust-x509-v1',now); freshness(roots,'cadevil-plugin-roots-x509-v1',now);
    if (expectedPluginId !== undefined && document.plugin_id !== expectedPluginId) throw Error('Plugin trust belongs to another plugin.');
    if (!Array.isArray(document.certificate_chain) || document.certificate_chain.length !== 3 || !Array.isArray(roots.roots) || roots.roots.length < 1 || roots.roots.length > 8) throw Error('Invalid plugin certificate chain.');
    const encoded = document.certificate_chain.map(value => base64Bytes(value));
    const [leaf,issuer,root] = encoded.map(bytes => Certificate.fromBER(bytes.buffer));
    certificatePolicy(leaf,false,null,now); certificatePolicy(issuer,true,0,now); certificatePolicy(root,true,1,now);
    const rootHash = await sha256(encoded[2],crypto);
    const anchor = roots.roots.find(value => value.id === rootHash);
    if (!anchor || anchor.certificate !== document.certificate_chain[2] || await sha256(base64Bytes(anchor.certificate),crypto) !== anchor.id) throw Error('Plugin certificate uses an unknown trust root.');
    if (!root.issuer.isEqual(root.subject) || !issuer.issuer.isEqual(root.subject) || !leaf.issuer.isEqual(issuer.subject)) throw Error('Invalid plugin certificate issuer chain.');
    const engine = new CryptoEngine({name:'cadevil-browser',crypto,subtle:crypto.subtle});
    setEngine('cadevil-browser',crypto,engine);
    if (!(await root.verify(root,engine)) || !(await issuer.verify(root,engine)) || !(await leaf.verify(issuer,engine))) throw Error('Invalid plugin certificate signature.');
    const crls = [await verifyCRL(document.leaf_crl,issuer,leaf,now,engine), await verifyCRL(document.issuer_crl,root,issuer,now,engine)];
    const path = new CertificateChainValidationEngine({certs:[root,issuer,leaf],trustedCerts:[root],crls,checkDate:now});
    const checked = await path.verify({passedWhenNotRevValues:false},engine);
    if (!checked.result || checked.certificatePath?.length !== 3) throw Error('Plugin certificate path validation failed.');
    const publicBytes = leaf.subjectPublicKeyInfo.subjectPublicKey.valueBlock.valueHexView;
    if (publicBytes.length !== 32 || leaf.subjectPublicKeyInfo.subjectPublicKey.valueBlock.unusedBits !== 0) throw Error('Invalid Ed25519 signing public key.');
    const signature = document.signature;
    if (!exact(signature,['format','algorithm','key_id','signature']) && !exact(signature,['format','algorithm','key_id','signature','certificate_chain'])) throw Error('Invalid plugin signature fields.');
    if (signature.certificate_chain !== undefined && (!Array.isArray(signature.certificate_chain) || signature.certificate_chain.length !== 3)) throw Error('Invalid archived certificate chain.');
    if (signature.certificate_chain) signature.certificate_chain.forEach(value => base64Bytes(value,32768));
    if (signature.format !== 'cadevil-plugin-signature-v1'
        || signature.algorithm !== 'Ed25519' || signature.key_id !== await sha256(publicBytes,crypto)) throw Error('Plugin signature key differs from its certificate.');
    publisherPolicy(leaf,document);
    let key;
    try {key = await crypto.subtle.importKey('raw',publicBytes,{name:'Ed25519'},false,['verify']);}
    catch(error) {if (error.name === 'NotSupportedError') throw Error('This browser does not support Ed25519 plugin signature verification.');throw error;}
    const signatureBytes = base64Bytes(signature.signature,64);
    if (signatureBytes.length !== 64 || !(await crypto.subtle.verify('Ed25519',key,signatureBytes,canonicalPackage(document.files)))) throw Error('The signed plugin file inventory was modified.');
    return {rootHash,keyId:signature.key_id,expires:Math.min(+leaf.notAfter.value,+issuer.notAfter.value,+root.notAfter.value,...crls.map(crl => +crl.nextUpdate.value)), files:document.files};
}
