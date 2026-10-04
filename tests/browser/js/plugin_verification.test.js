import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import {verifyPluginTrust,base64Bytes,canonicalPackage} from './plugin_verification.js';
import {loadVerifiedWorker,verifiedModuleGraph} from './verified_plugin_worker.js';
const fixture=JSON.parse(await readFile(new URL('./fixtures/plugin-trust.json',import.meta.url)));
const options={crypto:webcrypto,now:new Date(fixture.now),expectedPluginId:'cadevil.example.editor'};
const copy=value=>structuredClone(value);
const verify=(bundle=fixture.bundle,roots=fixture.roots,overrides={})=>verifyPluginTrust(bundle,roots,{...options,...overrides});
function corrupted(encoded) {const bytes=Buffer.from(encoded,'base64');bytes[bytes.length-1]^=1;return bytes.toString('base64');}

test('actual CA P256 issuer chain and Ed25519 file signature verify with fresh signed CRLs',async()=>{
    const proof=await verify();assert.equal(proof.rootHash,fixture.roots.roots[0].id);
    assert.equal(proof.keyId,fixture.bundle.signature.key_id);assert(proof.expires>+options.now);
});
test('missing anchors, changed certificates and issuer signatures fail closed',async()=>{
    const roots=copy(fixture.roots);roots.roots[0].id='0'.repeat(64);await assert.rejects(verify(fixture.bundle,roots),/unknown trust root/);
    for (const index of [0,1,2]) {
        const bundle=copy(fixture.bundle);bundle.certificate_chain[index]=corrupted(bundle.certificate_chain[index]);
        await assert.rejects(verify(bundle));
    }
    const two=copy(fixture.bundle);two.certificate_chain.pop();await assert.rejects(verify(two),/chain/);
});
test('revoked leaf, tampered CRLs and stale signed revocation evidence block execution',async()=>{
    const revoked=copy(fixture.bundle);revoked.leaf_crl=fixture.revoked_crl;await assert.rejects(verify(revoked),/revoked/);
    for (const name of ['leaf_crl','issuer_crl']) {const bundle=copy(fixture.bundle);bundle[name]=corrupted(bundle[name]);await assert.rejects(verify(bundle),/revocation/);}
    const future=new Date(+options.now+360000);
    const bundle=copy(fixture.bundle),roots=copy(fixture.roots);
    for(const item of [bundle,roots]) {item.generated_at=future.toISOString();item.expires_at=new Date(+future+300000).toISOString();}
    await assert.rejects(verify(bundle,roots,{now:future}),/revocation data is stale/);
});
test('wrong EKU, wrong key usage, changed key fingerprint and modified signed hashes are rejected',async()=>{
    for (const name of ['wrong_eku_leaf','wrong_usage_leaf']) {const bundle=copy(fixture.bundle);bundle.certificate_chain[0]=fixture[name];await assert.rejects(verify(bundle),/code-signing|key usage/);}
    const fingerprint=copy(fixture.bundle);fingerprint.signature.key_id='0'.repeat(64);await assert.rejects(verify(fingerprint),/key differs/);
    const signature=copy(fixture.bundle);signature.signature.signature=corrupted(signature.signature.signature);await assert.rejects(verify(signature),/file inventory/);
    const hash=copy(fixture.bundle);hash.files['worker.js']='0'.repeat(64);await assert.rejects(verify(hash),/file inventory/);
});
test('optional archived chain is bounded while the fresh current leaf remains authoritative',async()=>{
    const bundle=copy(fixture.bundle);bundle.signature.certificate_chain=[fixture.wrong_eku_leaf,...bundle.certificate_chain.slice(1)];await verify(bundle);
    bundle.signature.certificate_chain.push(bundle.certificate_chain[0]);await assert.rejects(verify(bundle),/archived certificate/);
});
test('unsupported crypto, wrong plugin identity, stale documents and noncanonical base64 fail closed',async()=>{
    await assert.rejects(verify(fixture.bundle,fixture.roots,{crypto:{}}),/cannot verify/);
    await assert.rejects(verify(fixture.bundle,fixture.roots,{expectedPluginId:'other'}),/another plugin/);
    await assert.rejects(verify(fixture.bundle,fixture.roots,{now:new Date(+options.now+360000)}),/stale/);
    assert.throws(()=>base64Bytes('Zg='),/encoding/);assert.throws(()=>base64Bytes('Zh=='),/encoding/);
});
test('canonical inventory matches Python lexical ordering even for numeric file names',()=>{
    const a='a'.repeat(64),b='b'.repeat(64);
    assert.equal(new TextDecoder().decode(canonicalPackage({'2':b,'10':a})),`{"context":"cadevil-plugin-package-v1","files":{"10":"${a}","2":"${b}"}}`);
    for(const files of [{'../bad.js':a},{'/bad.js':a},{'x.js':'A'.repeat(64)},{}])assert.throws(()=>canonicalPackage(files));
});
function graphFixture() {
    const blobs=[],revoked=[];
    return {blobs,revoked,options:{createObjectURL(blob){blobs.push(blob);return 'blob:verified-'+blobs.length},revokeObjectURL(url){revoked.push(url);}}};
}
const encoded=value=>new TextEncoder().encode(value);
test('maintained lexer rewrites signed static, reexport and literal dynamic imports to retained blobs',async()=>{
    const f=graphFixture();const files=new Map([['worker.js',encoded("import {value} from './dep.js'; export * from './dep.js';const lazy=()=>import('./dep.js');")],['dep.js',encoded('export const value=42;')]]);
    const graph=await verifiedModuleGraph(files,'worker.js',f.options);
    assert.equal(graph.entryURL,'blob:verified-2');assert.equal(f.revoked.length,0);
    assert.match(await f.blobs[1].text(),/from 'blob:verified-1'/);assert.match(await f.blobs[1].text(),/import\("blob:verified-1"\)/);
    graph.dispose();graph.dispose();assert.deepEqual(f.revoked,['blob:verified-1','blob:verified-2']);
});
test('lexer handles escaped literal imports and ignores import words in comments and strings',async()=>{
    const f=graphFixture();const graph=await verifiedModuleGraph(new Map([['worker.js',encoded("// import('unsigned.js')\n const text=\"import('unsigned.js')\";import './d\\x65p.js';")],['dep.js',encoded('export {};')]]),'worker.js',f.options);
    assert.match(await f.blobs[1].text(),/import 'blob:verified-1'/);graph.dispose();
});
test('nonliteral imports, import.meta, unsigned paths, external imports, attributes and cycles are rejected',async()=>{
    for (const source of ["import(name)","import(`./${name}.js`)","import.meta.url","import './missing.js'","import 'https://outside.test/x.js'","import './dep.js' with {type:'json'}","import '../escape.js'"]) {
        await assert.rejects(verifiedModuleGraph(new Map([['worker.js',encoded(source)],['dep.js',encoded('export {};')]]),'worker.js'));
    }
    await assert.rejects(verifiedModuleGraph(new Map([['worker.js',encoded("import './dep.js'")],['dep.js',encoded("import './worker.js'")]]),'worker.js'),/Cyclic/);
});
test('failed graph construction revokes any already-created dependency blob',async()=>{
    const f=graphFixture();await assert.rejects(verifiedModuleGraph(new Map([['worker.js',encoded("import './dep.js';import './missing.js'")],['dep.js',encoded('export {};')]]),'worker.js',f.options));
    assert.deepEqual(f.revoked,['blob:verified-1']);
});
function loaderFixture({changeFile=false,revoked=false}={}) {
    const requests=[],f=graphFixture();let isRevoked=revoked;
    const fetchFn=async(url,request)=>{
        requests.push({url,request});let bytes;
        if(url.endsWith('/roots.json')) bytes=encoded(JSON.stringify(fixture.roots));
        else if(url.endsWith('/trust.json')) {const bundle=copy(fixture.bundle);if(isRevoked)bundle.leaf_crl=fixture.revoked_crl;bytes=encoded(JSON.stringify(bundle));}
        else {const name=url.split('/').at(-1);bytes=Uint8Array.from(Buffer.from(fixture.files[name],'base64'));if(changeFile)bytes[0]^=1;}
        return new Response(bytes,{status:200});
    };
    const load=overrides=>loadVerifiedWorker({pluginId:'cadevil.example.editor',workerUrl:fixture.bundle.urls['worker.js'],baseURL:'https://app.example/',fetchFn,crypto:webcrypto,now:()=>options.now,...f.options,...overrides});
    return {...f,load,requests,revoke(){isRevoked=true;}};
}
test('verified loader hashes every file and returns only a restrictive core bootstrap plus blob entrypoint',async()=>{
    const f=loaderFixture();const result=await f.load();assert.equal(result.bootstrapURL,'https://app.example/plugins/worker-bootstrap.js');assert.equal(result.entryURL,'blob:verified-2');
    assert.equal(f.requests.length,4);assert(f.requests.every(item=>item.request.redirect==='error'&&item.request.cache==='no-store'));
    await result.recheck();assert.equal(f.requests.length,6);result.dispose();assert.equal(f.revoked.length,2);
});
test('hash-tampered signed artifact bytes never produce an executable blob',async()=>{
    const f=loaderFixture({changeFile:true});await assert.rejects(f.load(),/artifact bytes were modified/);assert.equal(f.blobs.length,0);
});
test('fresh revocation before Run blocks recheck of an already loaded worker',async()=>{
    const f=loaderFixture();const result=await f.load();f.revoke();await assert.rejects(result.recheck(),/revoked/);result.dispose();
});
test('cancelled, insecure-origin, mismatched worker and redirect responses cannot load a plugin',async()=>{
    const f=loaderFixture(),abort=new AbortController();abort.abort();await assert.rejects(f.load({signal:abort.signal}),/cancelled/);assert.equal(f.requests.length,0);
    await assert.rejects(f.load({baseURL:'http://outside.test/'}),/HTTPS/);
    await assert.rejects(f.load({workerUrl:'/unsigned.js'}),/differs/);
    await assert.rejects(f.load({fetchFn:async()=>({ok:true,redirected:true})}),/request failed/);
});
test('oversize fetches and cross-origin inventory URLs fail before any worker blob exists',async()=>{
    const f=loaderFixture();await assert.rejects(f.load({fetchFn:async()=>new Response('x',{headers:{'Content-Length':String(1024*1024)}})}),/exceeds/);
    const bundle=copy(fixture.bundle);bundle.urls['worker.js']='https://outside.test/worker.js';
    await assert.rejects(f.load({fetchFn:async url=>new Response(JSON.stringify(url.endsWith('/roots.json')?fixture.roots:bundle))}),/same-origin/);
    assert.equal(f.blobs.length,0);
});


test('server and uploaded publisher SAN scopes cannot substitute for each other',async()=>{
    const user=copy(fixture.user_bundle);await verify(user,fixture.roots,{expectedPluginId:'uploaded.fixture'});
    user.plugin_id='cadevil.example.editor';await assert.rejects(verify(user),/server publisher/);
    const server=copy(fixture.bundle);server.plugin_id='uploaded.fixture';await assert.rejects(verify(server,fixture.roots,{expectedPluginId:'uploaded.fixture'}),/scoped user/);
});
