// Reproduce the committed browser parser bundle from the pinned isolated toolchain.
import {readFile, writeFile, mkdir} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const tools = process.argv[2] && path.resolve(process.argv[2]);
if (!tools) throw Error('Usage: node scripts/vendor_browser_pki.mjs /path/to/isolated/browser-pki (npm ci --ignore-scripts first)');
const packages = ['pkijs','asn1js','pvtsutils','pvutils','bytestreamjs','tslib','@noble/hashes','es-module-lexer'];
for (const [name, expected] of Object.entries({pkijs:'3.4.1','es-module-lexer':'3.0.2',esbuild:'0.28.2'})) {
    const metadata = JSON.parse(await readFile(path.join(tools,'node_modules',name,'package.json'),'utf8'));
    if (metadata.version !== expected) throw Error('Unexpected browser toolchain version: '+name);
}
const destination = path.join(root,'resources/static/js/vendor/browser_pki.js');
await mkdir(path.dirname(destination),{recursive:true});
const entry = path.join(tools,'src/browser_pki.js');
await mkdir(path.dirname(entry),{recursive:true});
await writeFile(entry,"export {Certificate, CertificateRevocationList, CertificateChainValidationEngine, CryptoEngine, setEngine} from 'pkijs';\nexport {init, parse} from 'es-module-lexer/js';\n");
const result = spawnSync(process.execPath,[path.join(tools,'node_modules/esbuild/bin/esbuild'),entry,'--bundle','--format=esm','--platform=browser','--target=es2022','--tree-shaking=true','--minify','--legal-comments=none','--outfile='+destination],{stdio:'inherit'});
if (result.status !== 0) process.exit(result.status ?? 1);
const licenses=[];
for (const name of packages) {
    const directory=path.join(tools,'node_modules',name);
    const metadata=JSON.parse(await readFile(path.join(directory,'package.json'),'utf8'));
    let license;
    for (const filename of ['LICENSE','LICENSE.txt','LICENSE.md','LICENSE-MIT.txt','CopyrightNotice.txt']) {
        try {license=await readFile(path.join(directory,filename),'utf8');break;} catch(error) {if(error.code!=='ENOENT')throw error;}
    }
    if (!license) throw Error('Missing vendor license: '+name);
    licenses.push(name+' '+metadata.version+' ('+metadata.license+')\n'+license.trim());
}
await writeFile(destination+'.LICENSE.txt',licenses.join('\n\n--------------------\n\n')+'\n');
