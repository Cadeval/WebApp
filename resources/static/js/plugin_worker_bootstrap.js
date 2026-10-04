// Trusted core script runs under its own response CSP: no network or child workers.
let booted = false;
async function boot(event) {
    const data = event.data;
    if (booted || data?.type !== 'cadevil-verified-boot' || typeof data.entry_url !== 'string'
        || !data.entry_url.startsWith('blob:') || !data.initialize || typeof data.initialize !== 'object') {
        self.postMessage({type:'error',message:'Invalid verified worker bootstrap.'});
        return;
    }
    booted = true;
    self.removeEventListener('message',boot);
    try {
        await import(data.entry_url);
        self.dispatchEvent(new MessageEvent('message',{data:data.initialize}));
    } catch {
        self.postMessage({type:'error',message:'Verified plugin worker could not initialize.'});
    }
}
self.addEventListener('message',boot);
