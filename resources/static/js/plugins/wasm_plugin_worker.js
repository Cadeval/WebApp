export const MAX_WASM_BYTES = 2 * 1024 * 1024;
export const CALCULATION_EXPORTS = ['calculate', 'double'];
function exactKeys(message, keys) {
    return message && !Array.isArray(message) && typeof message === 'object'
        && Object.keys(message).length === keys.length && keys.every(key => key in message);
}
export function createWasmHandler({send, instantiate = WebAssembly.instantiate} = {}) {
    let calculate = null;
    let initialized = false;
    return async message => {
        try {
            if (message?.type === 'initialize' && exactKeys(message, ['type', 'wasmBytes'])) {
                if (initialized) throw new Error('Plugin is already initialized. Reload its worker to initialize again.');
                initialized = true;
                const bytes = message.wasmBytes;
                if (!(bytes instanceof ArrayBuffer) || bytes.byteLength < 8 || bytes.byteLength > MAX_WASM_BYTES) throw new Error('Invalid WebAssembly size.');
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
