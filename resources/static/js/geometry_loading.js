// Progress reports measured transfer bytes only; server preparation and parsing
// remain indeterminate because neither task exposes a reliable total.
export function createGeometryActivity({ target, overlay, message, detail, progress, label = '3D model', activity = globalThis.CadevilActivity }) {
    let finished = false;
    const token = activity?.start({ label: `Preparing ${label}`, target });
    if (token == null) target?.setAttribute('aria-busy', 'true');
    if (overlay) overlay.hidden = false;
    const names = {
        preparing: `Preparing ${label}…`,
        downloading: `Downloading ${label}…`,
        processing: `Processing ${label} geometry…`,
        rendering: `Rendering ${label}…`,
    };
    function stage(name) {
        if (finished) return;
        const value = names[name] || name;
        if (message) message.textContent = value;
        if (detail) detail.textContent = name === 'preparing' ? 'Preparing geometry can take a little longer on the first load.' : '';
        if (progress) {
            progress.removeAttribute('value');
            progress.removeAttribute('aria-valuetext');
            progress.setAttribute('aria-label', name === 'downloading' ? 'Geometry download progress' : value);
        }
        if (token != null) activity?.update(token, { label: value });
    }
    function transfer({ loaded, total }) {
        if (finished) return;
        const known = Number.isFinite(total) && total > 0 && loaded <= total;
        const bytes = `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(loaded / 1048576)} MB received`;
        if (known) {
            const percent = Math.min(100, Math.floor(loaded / total * 100));
            if (progress) {
                progress.value = percent;
                progress.setAttribute('aria-valuetext', `${percent}% downloaded`);
            }
            if (detail) detail.textContent = `${percent}% downloaded · ${bytes}`;
        } else {
            progress?.removeAttribute('value');
            if (detail) detail.textContent = bytes;
        }
    }
    function finish() {
        if (finished) return;
        finished = true;
        if (token != null) activity?.finish(token);
        if (token == null) target?.setAttribute('aria-busy', 'false');
        if (overlay) overlay.hidden = true;
    }
    stage('preparing');
    return { stage, transfer, finish };
}

export function yieldToPaint() {
    return new Promise(resolve => {
        if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => requestAnimationFrame(resolve));
        else setTimeout(resolve, 0);
    });
}

export async function loadGeometry(url, { loader, signal, onStage = () => {}, onProgress = () => {}, fetchImpl = globalThis.fetch, beforeParse = yieldToPaint } = {}) {
    onStage('preparing');
    const response = await fetchImpl(url, { signal, credentials: 'same-origin' });
    if (!response.ok) throw new Error(`The 3D model could not be loaded (${response.status}).`);
    signal?.throwIfAborted();
    onStage('downloading');
    // Content-Encoding can make Content-Length refer to compressed bytes. Only
    // display a percentage when the received stream and declared size agree.
    const rawLength = response.headers.get('content-length');
    const encoding = response.headers.get('content-encoding');
    const total = !encoding && /^\d+$/.test(rawLength || '') ? Number(rawLength) : null;
    let buffer;
    if (response.body?.getReader) {
        const reader = response.body.getReader(), chunks = [];
        let loaded = 0;
        try {
            while (true) {
                const result = await reader.read();
                signal?.throwIfAborted();
                if (result.done) break;
                loaded += result.value.byteLength;
                chunks.push(result.value);
                onProgress({ loaded, total });
            }
        } finally {
            if (signal?.aborted) await reader.cancel().catch(() => {});
            reader.releaseLock();
        }
        const bytes = new Uint8Array(loaded);
        let offset = 0;
        for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
        buffer = bytes.buffer;
    } else {
        buffer = await response.arrayBuffer();
        onProgress({ loaded: buffer.byteLength, total });
    }
    signal?.throwIfAborted();
    onStage('processing');
    await beforeParse();
    signal?.throwIfAborted();
    // Preserve GLTFLoader's original base URL for any authored relative assets.
    const resourcePath = new URL('.', response.url || url).href;
    return loader.parseAsync(buffer, resourcePath);
}
