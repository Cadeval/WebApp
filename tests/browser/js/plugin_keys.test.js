import assert from 'node:assert/strict';
import test from 'node:test';
import {webcrypto} from 'node:crypto';
import {createEncryptedKey} from './plugin_keys.js';

test('browser key encryption and registration proof interoperate with WebCrypto',async()=>{
 const passphrase='disposable-test-passphrase';const challenge='one-use-challenge';const owner='17';
 const result=await createEncryptedKey({crypto:webcrypto,passphrase,challenge,owner});
 const file=result.file;const decode=value=>Buffer.from(value,'base64');
 assert.equal(file.encryption.iterations,600000);assert.equal(decode(file.encrypted_private_key).length,64);
 const password=await webcrypto.subtle.importKey('raw',new TextEncoder().encode(passphrase),'PBKDF2',false,['deriveKey']);
 const wrapping=await webcrypto.subtle.deriveKey({name:'PBKDF2',hash:'SHA-256',salt:decode(file.encryption.salt),iterations:file.encryption.iterations},password,{name:'AES-GCM',length:256},false,['decrypt']);
 const pkcs8=await webcrypto.subtle.decrypt({name:'AES-GCM',iv:decode(file.encryption.iv)},wrapping,decode(file.encrypted_private_key));
 const privateKey=await webcrypto.subtle.importKey('pkcs8',pkcs8,'Ed25519',false,['sign']);
 const publicKey=await webcrypto.subtle.importKey('raw',decode(result.publicKey),'Ed25519',false,['verify']);
 const proofPayload=new TextEncoder().encode(JSON.stringify({challenge,context:'cadevil-key-registration-v1',owner,public_key:result.publicKey}));
 assert.equal(await webcrypto.subtle.verify('Ed25519',publicKey,decode(result.proof),proofPayload),true);
 const signed=await webcrypto.subtle.sign('Ed25519',privateKey,proofPayload);
 assert.equal(await webcrypto.subtle.verify('Ed25519',publicKey,signed,proofPayload),true);
 assert.equal(Buffer.from(await webcrypto.subtle.digest('SHA-256',decode(result.publicKey))).toString('hex'),file.key_id);
 const modified=decode(file.encrypted_private_key);modified[0]^=1;
 await assert.rejects(webcrypto.subtle.decrypt({name:'AES-GCM',iv:decode(file.encryption.iv)},wrapping,modified));
});

test('team registration proof binds the selected team and uses the v2 context', async () => {
 const team_id='a040f3b8-3aa9-437f-9b42-66c33020621e';
 const result=await createEncryptedKey({crypto:webcrypto,passphrase:'disposable-team-passphrase',challenge:'team-challenge',owner:'17',team_id});
 const publicKey=await webcrypto.subtle.importKey('raw',Buffer.from(result.publicKey,'base64'),'Ed25519',false,['verify']);
 const proof=Buffer.from(result.proof,'base64');
 const payload=(team)=>new TextEncoder().encode(JSON.stringify({challenge:'team-challenge',context:'cadevil-key-registration-v2',owner:'17',public_key:result.publicKey,team_id:team}));
 assert.equal(await webcrypto.subtle.verify('Ed25519',publicKey,proof,payload(team_id)),true);
 assert.equal(await webcrypto.subtle.verify('Ed25519',publicKey,proof,payload('another-team')),false);
});
