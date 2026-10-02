export const MAX_WASM_BYTES = 2 * 1024 * 1024;
export const CALCULATION_EXPORTS = ['calculate', 'double'];
function exactKeys(message, keys) {
    return message && !Array.isArray(message) && typeof message === 'object'
        && Object.keys(message).length === keys.length && keys.every(key => key in message);
}
export function createWasmHandler({send, fetchWasm = globalThis.fetch?.bind(globalThis),
    instantiate = WebAssembly.instantiate, baseUrl = globalThis.location?.href} = {}) {
    let calculate = null;
    let initialized = false;
    return async message => {
        try {
            if (message?.type === 'initialize' && exactKeys(message, ['type', 'wasmUrl'])) {
                if (initialized) throw new Error('Plugin is already initialized. Reload its worker to initialize again.');
                if (typeof message.wasmUrl !== 'string' || !message.wasmUrl || message.wasmUrl.length > 2048) throw new Error('Invalid WebAssembly URL.');
                const base = new URL(baseUrl);
                const url = new URL(message.wasmUrl, base);
                if (!['http:', 'https:'].includes(url.protocol) || url.origin !== base.origin || url.username || url.password) throw new Error('WebAssembly must use a same-origin HTTP URL.');
                initialized = true;
                const response = await fetchWasm(url.href, {credentials: 'same-origin', redirect: 'error', cache: 'no-store'});
                if (!response.ok || response.redirected) throw new Error(`WebAssembly request failed (${response.status}).`);
                const advertised = Number(response.headers?.get('content-length'));
                if (advertised > MAX_WASM_BYTES) throw new Error('WebAssembly exceeds the upload size limit.');
                const bytes = await response.arrayBuffer();
                if (bytes.byteLength < 8 || bytes.byteLength > MAX_WASM_BYTES) throw new Error('Invalid WebAssembly size.');
                const result = await instantiate(bytes, {});
                const exports = result.instance.exports;
                const name = CALCULATION_EXPORTS.find(name => typeof exports[name] === 'function');
                if (!name) throw new Error('WebAssembly must export calculate(number) or the legacy double(number) function.');
                calculate = exports[name];
                send({type: 'ready'});
            } else if (message?.type === 'run' && exactKeys(message, ['type', 'value']) && Number.isFinite(message.value)) {
                if (!calculate) throw new Error('WebAssembly plugin is not initialized.');
                const value = calculate(message.value);
                if (!Number.isFinite(value)) throw new Error('WebAssembly calculation must return a finite number.');
                send({type: 'result', value});
            } else throw new Error('Invalid host message.');
        } catch (error) {
            calculate = null;
            send({type: 'error', message: String(error.message || 'Plugin execution failed.').slice(0, 2000)});
        }
    };
}
if (typeof self !== 'undefined' && typeof self.postMessage === 'function') {
    const handle = createWasmHandler({send: message => self.postMessage(message)});
    self.onmessage = event => handle(event.data);
}
