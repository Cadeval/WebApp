import {init,parse} from './vendor/browser_pki.js';
import {sha256,verifyPluginTrust,canonicalPackage,isBundledPluginId} from './plugin_verification.js';
const decoder = new TextDecoder('utf-8',{fatal:true});
const MAX_FILE_BYTES = 2 * 1024 * 1024;
const MAX_TOTAL_BYTES = 8 * 1024 * 1024;
const MAX_TRUST_BYTES = 128 * 1024;

function sameOrigin(value,base) {
    const url = new URL(value,base);
    if (!value || !['https:','http:'].includes(url.protocol) || url.origin !== new URL(base).origin || url.username || url.password || url.hash) throw Error('Plugin verification requires same-origin artifact URLs.');
    return url.href;
}
function checkSecureOrigin(base) {
    const url = new URL(base);
    if (url.protocol !== 'https:' && !['localhost','127.0.0.1','[::1]'].includes(url.hostname)) throw Error('Plugin verification requires HTTPS.');
}
async function boundedFetch(url,limit,fetchFn,signal) {
    const response = await fetchFn(url,{credentials:'same-origin',cache:'no-store',redirect:'error',signal});
    if (response.status === 503) throw Error('Plugin signing trust is unavailable. Ask an administrator to restore its certificates.');
    if ([401,403].includes(response.status)) throw Error('Access to this plugin is unavailable. Sign in and check your workflow selection.');
    if (!response.ok || response.redirected || response.type === 'opaque') throw Error('Plugin verification request failed.');
    const advertised = Number(response.headers?.get('content-length'));
    if (Number.isFinite(advertised) && advertised > limit) throw Error('Plugin verification document exceeds its limit.');
    if (response.body?.getReader) {
        const reader = response.body.getReader(); let size=0; const parts=[];
        try {
            while (true) {
                const {done,value} = await reader.read(); if (done) break;
                size += value.byteLength; if (size > limit) throw Error('Plugin verification document exceeds its limit.'); parts.push(value);
            }
        } catch(error) {await reader.cancel().catch(() => {});throw error;}
        const bytes = new Uint8Array(size); let offset=0; for (const part of parts) {bytes.set(part,offset);offset+=part.byteLength;}
        return bytes;
    }
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes.byteLength > limit) throw Error('Plugin verification document exceeds its limit.');
    return bytes;
}
async function jsonFetch(url,fetchFn,signal) {
    const bytes = await boundedFetch(url,MAX_TRUST_BYTES,fetchFn,signal);
    return JSON.parse(decoder.decode(bytes));
}
function dependencyName(specifier,from,files) {
    if (typeof specifier !== 'string' || !specifier.startsWith('.') || /[\\%?#:]/.test(specifier)) throw Error('Plugin imports must name signed relative files.');
    const base = new URL(from,'https://signed.invalid/');
    const resolved = new URL(specifier,base);
    const name = resolved.pathname.slice(1);
    if (resolved.origin !== base.origin || !Object.hasOwn(files,name) || !/\.(?:js|mjs)$/.test(name)) throw Error('Plugin imports an unsigned or unsupported dependency.');
    return name;
}
export async function verifiedModuleGraph(files,entrypoint,{createObjectURL = blob => URL.createObjectURL(blob),revokeObjectURL = url => URL.revokeObjectURL(url),BlobClass=Blob} = {}) {
    await init();
    const urls = new Map(), visiting = new Set();
    function visit(name) {
        if (urls.has(name)) return urls.get(name);
        if (visiting.has(name)) throw Error('Cyclic plugin imports are unsupported.');
        if (!files.has(name) || !/\.(?:js|mjs)$/.test(name)) throw Error('The signed worker entrypoint is missing.');
        visiting.add(name);
        const source = decoder.decode(files.get(name));
        const imports = parse(source)[0], changes=[];
        for (const item of imports) {
            if (item.typeOnly || item.probablyTypeOnly || item.phase || item.attributesStart !== -1 || item.glob
                || item.dynamicStart === -2 || typeof item.specifier !== 'string') throw Error('Plugin has a nonliteral or unsupported import.');
            const dependency = dependencyName(item.specifier,name,Object.fromEntries(files));
            const url = visit(dependency);
            changes.push({start:item.start,end:item.end,value:item.type === 'dynamic' ? JSON.stringify(url) : url});
        }
        let rewritten=source;
        for (const change of changes.sort((a,b) => b.start-a.start)) rewritten=rewritten.slice(0,change.start)+change.value+rewritten.slice(change.end);
        const url = createObjectURL(new BlobClass([rewritten],{type:'application/javascript'}));
        urls.set(name,url); visiting.delete(name); return url;
    }
    try {
        const entryURL=visit(entrypoint);
        return {entryURL,dispose() {for (const url of urls.values()) revokeObjectURL(url);urls.clear();}};
    } catch(error) {for (const url of urls.values()) revokeObjectURL(url);throw error;}
}
export async function loadVerifiedWorker({pluginId,trustUrl,workerUrl,wasmUrl='',signal,
    fetchFn=globalThis.fetch?.bind(globalThis),crypto=globalThis.crypto,now=() => new Date(),
    baseURL=globalThis.location?.href || 'http://localhost/',...graphOptions} = {}) {
    checkSecureOrigin(baseURL);
    if (!fetchFn || !crypto?.subtle || typeof pluginId !== 'string' || !/^[a-z0-9]+(?:[._-][a-z0-9]+)*$/.test(pluginId)) throw Error('This browser cannot verify plugin artifacts.');
    const trustURL=sameOrigin(trustUrl || '/plugins/'+encodeURIComponent(pluginId)+'/trust.json',baseURL);
    const rootsURL=sameOrigin('/plugins/trust/roots.json',baseURL);
    let disposed=false;
    const alive = () => {if (disposed || signal?.aborted) throw Error('Plugin verification was cancelled.');};
    async function trust() {
        alive();
        const [document,roots] = await Promise.all([jsonFetch(trustURL,fetchFn,signal),jsonFetch(rootsURL,fetchFn,signal)]);
        const proof = await verifyPluginTrust(document,roots,{crypto,now:now(),expectedPluginId:pluginId});
        let wrapperProof;
        if (document.wrapper) wrapperProof = await verifyPluginTrust(document.wrapper,roots,{crypto,now:now(),expectedPluginId:'cadevil.browser.wasm-wrapper'});
        alive();return {document,proof,wrapperProof};
    }
    const initial=await trust();
    const documents=[initial.document,...(initial.document.wrapper ? [initial.document.wrapper] : [])];
    const inventories=[];let total=0;
    for (const document of documents) {
        if (!document.urls || Array.isArray(document.urls) || typeof document.urls !== 'object' || Object.keys(document.urls).length !== Object.keys(document.files).length) throw Error('Plugin artifact URL inventory differs from its signed files.');
        const files=new Map();
        for (const [name,hash] of Object.entries(document.files)) {
            alive();const url=sameOrigin(document.urls[name],baseURL);
            const bytes=await boundedFetch(url,MAX_FILE_BYTES,fetchFn,signal);total+=bytes.byteLength;
            if (total > MAX_TOTAL_BYTES || await sha256(bytes,crypto) !== hash) throw Error('Signed plugin artifact bytes were modified.');
            files.set(name,bytes);
        }
        inventories.push(files);
    }
    const document=initial.document;
    if (!isBundledPluginId(pluginId) && !inventories[0].has('plugin.json')) throw Error('Uploaded plugin is missing its signed manifest.');
    if (inventories[0].has('plugin.json')) {
        const manifest=JSON.parse(decoder.decode(inventories[0].get('plugin.json')));
        if (manifest.id !== pluginId || manifest.entrypoint !== document.entrypoint
            || !['javascript','wasm'].includes(manifest.type) || (manifest.type === 'wasm') !== Boolean(document.wasm)) throw Error('Plugin entrypoint differs from its signed manifest.');
    }
    const execution=document.wrapper || document;
    if (document.wrapper && (!document.wasm || document.wrapper.wasm)) throw Error('Invalid verified WASM wrapper.');
    if (new URL(sameOrigin(workerUrl,baseURL)).pathname !== new URL(sameOrigin(execution.urls[execution.entrypoint],baseURL)).pathname) throw Error('The worker URL differs from its signed entrypoint.');
    let wasmBytes;
    if (document.wasm) {
        if (!inventories[0].has(document.wasm) || !wasmUrl || new URL(sameOrigin(wasmUrl,baseURL)).pathname !== new URL(sameOrigin(document.urls[document.wasm],baseURL)).pathname) throw Error('The WebAssembly URL differs from its signed entrypoint.');
        wasmBytes=inventories[0].get(document.wasm).slice().buffer;
    } else if (wasmUrl) throw Error('Unsigned WebAssembly input.');
    alive();
    if (+now() >= Math.min(initial.proof.expires,initial.wrapperProof?.expires ?? Infinity)) throw Error('Plugin revocation evidence expired during verification.');
    const graph=await verifiedModuleGraph(inventories.at(-1),execution.entrypoint,graphOptions);
    try {alive();} catch(error) {graph.dispose();throw error;}
    const filesIdentity=new TextDecoder().decode(canonicalPackage(document.files));
    const wrapperIdentity=document.wrapper && new TextDecoder().decode(canonicalPackage(document.wrapper.files));
    let expires=Math.min(initial.proof.expires,initial.wrapperProof?.expires ?? Infinity);
    return {entryURL:graph.entryURL,bootstrapURL:sameOrigin('/plugins/worker-bootstrap.js',baseURL),wasmBytes,
        get expires() {return expires;},
        async recheck() {
            const current=await trust();
            if (current.proof.keyId !== initial.proof.keyId || current.proof.rootHash !== initial.proof.rootHash
                || new TextDecoder().decode(canonicalPackage(current.document.files)) !== filesIdentity
                || Boolean(current.document.wrapper) !== Boolean(document.wrapper)
                || (document.wrapper && (current.wrapperProof.keyId !== initial.wrapperProof.keyId || current.wrapperProof.rootHash !== initial.wrapperProof.rootHash
                    || new TextDecoder().decode(canonicalPackage(current.document.wrapper.files)) !== wrapperIdentity))) throw Error('Plugin trust changed. Reload the worker before continuing.');
            alive();expires=Math.min(current.proof.expires,current.wrapperProof?.expires ?? Infinity);
            if (+now() >= expires) throw Error('Plugin revocation evidence expired during verification.');
            return true;
        },
        dispose() {if (disposed) return;disposed=true;graph.dispose();},
    };
}
