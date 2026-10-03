function base64(bytes) { return btoa(String.fromCharCode(...new Uint8Array(bytes))); }
export async function createEncryptedKey({crypto,passphrase,challenge,owner}) {
    const keys=await crypto.subtle.generateKey({name:'Ed25519'},true,['sign','verify']);
    const publicBytes=await crypto.subtle.exportKey('raw',keys.publicKey);
    const publicKey=base64(publicBytes);
    const privateBytes=await crypto.subtle.exportKey('pkcs8',keys.privateKey);
    const fingerprint=[...new Uint8Array(await crypto.subtle.digest('SHA-256',publicBytes))].map(value=>value.toString(16).padStart(2,'0')).join('');
    const salt=crypto.getRandomValues(new Uint8Array(16));const iv=crypto.getRandomValues(new Uint8Array(12));
    const password=await crypto.subtle.importKey('raw',new TextEncoder().encode(passphrase),'PBKDF2',false,['deriveKey']);
    const wrappingKey=await crypto.subtle.deriveKey({name:'PBKDF2',hash:'SHA-256',salt,iterations:600000},password,{name:'AES-GCM',length:256},false,['encrypt']);
    const encrypted=await crypto.subtle.encrypt({name:'AES-GCM',iv},wrappingKey,privateBytes);
    new Uint8Array(privateBytes).fill(0);
    const proofPayload=JSON.stringify({challenge,context:'cadevil-key-registration-v1',owner,public_key:publicKey});
    const proof=base64(await crypto.subtle.sign('Ed25519',keys.privateKey,new TextEncoder().encode(proofPayload)));
    return {publicKey,proof,file:{format:'cadevil-signing-key-v1',algorithm:'Ed25519',key_id:fingerprint,public_key:publicKey,encryption:{algorithm:'AES-256-GCM',kdf:'PBKDF2-SHA256',iterations:600000,salt:base64(salt),iv:base64(iv)},encrypted_private_key:base64(encrypted)}};
}
function mount(root=document) {
    const form=root.querySelector('[data-signing-key-form]');
    if (!form || form.dataset.mounted) return;
    form.dataset.mounted='true';
    form.querySelector('[data-key-controls]').disabled=false;
    form.addEventListener('submit',async event=>{
        event.preventDefault();const status=form.querySelector('[role="status"]');const button=form.querySelector('button[type="submit"]');
        const password=form.querySelector('[name="passphrase"]');const confirm=form.querySelector('[name="confirm"]');
        if(password.value.length<12 || password.value!==confirm.value){status.textContent='Use a passphrase of at least 12 characters and enter it twice.';return;}
        if(!globalThis.isSecureContext || !globalThis.crypto?.subtle){status.textContent='Key generation needs HTTPS or localhost and a browser supporting Ed25519.';return;}
        button.disabled=true;status.textContent='Creating and encrypting your key locally…';
        try {
            const registration=JSON.parse(document.getElementById('key-registration').textContent);
            const key=await createEncryptedKey({crypto:globalThis.crypto,passphrase:password.value,...registration});
            password.value='';confirm.value='';
            const response=await fetch(form.action,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':form.querySelector('[name="csrfmiddlewaretoken"]').value},body:JSON.stringify({label:form.querySelector('[name="label"]').value,public_key:key.publicKey,proof:key.proof,challenge:registration.challenge})});
            const result=await response.json();if(!response.ok)throw new Error(result.error || 'Key registration failed.');
            if(result.key_id!==key.file.key_id)throw new Error('The registered key id did not match.');
            const blob=new Blob([JSON.stringify(key.file,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);
            const link=document.createElement('a');link.href=url;link.download=`${result.key_id.slice(0,16)}.cadevil-key.json`;link.textContent='Download encrypted private key again';
            const output=form.querySelector('[data-key-download]');output.replaceChildren(link);link.click();
            // Keep the download available for this page only; never store private material in browser storage.
            globalThis.addEventListener('pagehide',()=>URL.revokeObjectURL(url),{once:true});
            status.textContent=`Key registered: ${result.key_id}. Save the encrypted key file and keep your passphrase safe. Reload this page to create another key.`;
        } catch(error){status.textContent=error.message || 'Key creation failed. Your browser may not support Ed25519.';button.disabled=false;}
    });
}
if(typeof document!=='undefined') {
    if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>mount());else mount();
    document.body?.addEventListener('htmx:after:settle',()=>mount());
}
