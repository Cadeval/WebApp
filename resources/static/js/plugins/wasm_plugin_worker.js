let wasmInstance = null;

self.onmessage = async (event) => {
    const message = event.data;
    if (!message || typeof message !== 'object') {
        postMessage({ type: 'error', message: 'Invalid host message.' });
        return;
    }
    try {
        if (message.type === 'initialize' && typeof message.wasmUrl === 'string') {
            const response = await fetch(message.wasmUrl, { credentials: 'same-origin' });
            if (!response.ok) throw new Error(`WebAssembly request failed (${response.status}).`);
            const bytes = await response.arrayBuffer();
            const result = await WebAssembly.instantiate(bytes, {});
            wasmInstance = result.instance;
            postMessage({ type: 'ready' });
        } else if (message.type === 'run' && Number.isFinite(message.value)) {
            if (!wasmInstance) throw new Error('WebAssembly plugin is not initialized.');
            const callable = Object.values(wasmInstance.exports).find(
                (value) => typeof value === 'function',
            );
            if (!callable) throw new Error('WebAssembly plugin exports no callable function.');
            postMessage({ type: 'result', value: callable(message.value) });
        } else {
            throw new Error('Unsupported host message.');
        }
    } catch (error) {
        postMessage({ type: 'error', message: error.message || 'Plugin execution failed.' });
    }
};