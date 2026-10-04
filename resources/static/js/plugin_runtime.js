import {prepareVerifiedWorker as loadVerifiedWorker,settleVerification} from './worker_page_lifecycle.js';

export function withReloadVersion(url, generation) {
    const [path, hash] = url.split('#');
    return `${path}${path.includes('?') ? '&' : '?'}cadevilPluginReload=${generation}${hash === undefined ? '' : `#${hash}`}`;
}
function control(element, kind, name) {
    return element.querySelector(`[data-plugin-${kind}="${name}"]`);
}
export class EditorPluginRuntime {
    constructor({ WorkerClass = globalThis.Worker, setTimer = globalThis.setTimeout?.bind(globalThis),
        clearTimer = globalThis.clearTimeout?.bind(globalThis), maxRunMs = 2000, logger = globalThis.console,
        baseUrl = globalThis.location?.href || 'http://localhost/', prepareWorker=loadVerifiedWorker } = {}) {
        Object.assign(this, { WorkerClass, setTimer, clearTimer, maxRunMs, logger, baseUrl,prepareWorker });
        this.controllers = new Map();
    }
    mount(root = globalThis.document) {
        if (!root?.querySelectorAll || !this.WorkerClass) return;
        const elements = [...root.querySelectorAll('[data-editor-plugin]')];
        for (const controller of [...this.controllers.values()]) {
            if (!elements.includes(controller.element)) this.stop(controller);
        }
        for (const element of elements) {
            const pluginId = element.dataset.editorPlugin;
            if (this.controllers.get(pluginId)?.element === element) continue;
            if (this.controllers.has(pluginId)) this.stop(this.controllers.get(pluginId));
            try {
                const controller = { pluginId, element, generation: 0, worker: null,
                    runTimer: null, phase: 'stopped', listeners: [],
                    runButton: control(element, 'action', 'run'), reloadButton: control(element, 'action', 'reload'),
                    input: control(element, 'field', 'input'), status: control(element, 'field', 'status'),
                    result: control(element, 'field', 'result'), generationLabel: control(element, 'field', 'generation') };
                if (!pluginId || ['runButton', 'reloadButton', 'input', 'status', 'result', 'generationLabel'].some(key => !controller[key])) {
                    throw new Error('Plugin panel is missing required controls.');
                }
                for (const [button, callback] of [[controller.runButton, () => this.run(controller)],
                    [controller.reloadButton, () => this.start(controller)]]) {
                    button.addEventListener('click', callback);
                    controller.listeners.push([button, callback]);
                }
                this.controllers.set(pluginId, controller);
                this.start(controller);
            } catch (error) { this.logger?.error?.(`[plugin-manager] ${pluginId}: ${error.message}`); }
        }
    }
    validateUrl(value) {
        const base = new URL(this.baseUrl);
        const url = new URL(value, base);
        if (!value || !['http:', 'https:'].includes(url.protocol) || url.origin !== base.origin || url.username || url.password) {
            throw new Error('Plugin artifacts must use same-origin HTTP URLs.');
        }
        return value;
    }
    limit(controller) {
        const configured = Number(controller.element.dataset.maxRunMs);
        return Math.min(30000, Number.isFinite(configured) && configured > 0 ? configured : this.maxRunMs);
    }
    watch(controller, label, maximum) {
        this.clearRunTimer(controller);
        const worker = controller.worker;
        const generation = controller.generation;
        const limit = maximum ?? this.limit(controller);
        controller.runTimer = this.setTimer(() => {
            if (controller.worker === worker && controller.generation === generation && this.controllers.get(controller.pluginId) === controller) this.handleError(controller, `${label} timed out after ${limit} ms.`);
        }, limit);
    }
    releaseWorker(controller) {
        controller.verificationAbort?.abort();
        controller.verificationAbort = null;
        controller.verified?.dispose();
        controller.verified = null;
        this.clearRunTimer(controller);
        if (controller.worker) {
            controller.worker.onmessage = null;
            controller.worker.onerror = null;
            controller.worker.onmessageerror = null;
            controller.worker.terminate();
            controller.worker = null;
        }
    }
    start(controller) {
        this.releaseWorker(controller);
        controller.generation += 1;
        controller.phase = 'loading';
        controller.runButton.disabled = true;
        controller.status.textContent = 'Verifying plugin certificate and signed files…';
        controller.result.textContent = '—';
        controller.generationLabel.textContent = `Generation ${controller.generation}`;
        try {
            const url = this.validateUrl(controller.element.dataset.workerUrl);
            const wasm = controller.element.dataset.wasmUrl || '';
            if (wasm) this.validateUrl(wasm);
            const generation = controller.generation;
            const abort = new AbortController();
            controller.verificationAbort = abort;
            const currentGeneration = () => controller.generation === generation && this.controllers.get(controller.pluginId) === controller && !abort.signal.aborted;
            this.watch(controller, 'Plugin verification',10000);
            settleVerification(this.prepareWorker({pluginId:controller.pluginId,trustUrl:controller.element.dataset.trustUrl,
                workerUrl:url,wasmUrl:wasm,signal:controller.verificationAbort.signal,baseURL:this.baseUrl}), verified => {
            if (!currentGeneration()) {verified.dispose();return;}
            controller.verified = verified;
            const worker = new this.WorkerClass(withReloadVersion(verified.bootstrapURL, controller.generation), { type: 'module', name: controller.pluginId });
            controller.worker = worker;
            const current = () => controller.worker === worker && this.controllers.get(controller.pluginId) === controller;
            worker.onmessage = event => { if (current()) this.handleMessage(controller, event.data); };
            worker.onerror = event => { if (current()) this.handleError(controller, event.message); };
            worker.onmessageerror = () => { if (current()) this.handleError(controller, 'Invalid worker message.'); };
            this.watch(controller, 'Plugin initialization');
            const bytes = verified.wasmBytes?.slice(0);
            worker.postMessage({type:'cadevil-verified-boot',entry_url:verified.entryURL,
                initialize:{ type: 'initialize', ...(bytes ? {wasmBytes:bytes} : {}) }},bytes ? [bytes] : []);
            }, error => {if (currentGeneration()) this.handleError(controller,error.message);});
        } catch (error) { this.handleError(controller, error.message); }
    }
    run(controller) {
        if (controller.phase !== 'ready' || !controller.worker) return;
        const value = Number(controller.input.value);
        if (!Number.isFinite(value)) {
            controller.status.textContent = 'Enter a finite number.';
            return;
        }
        controller.phase = 'running';
        controller.runButton.disabled = true;
        controller.status.textContent = 'Calculating in worker…';
        try {
            const worker=controller.worker;
            this.watch(controller,'Plugin verification',10000);
            settleVerification(controller.verified.recheck(), () => {
                if (controller.worker !== worker || controller.phase !== 'running') return;
                this.watch(controller, 'Plugin calculation');
                worker.postMessage({ type: 'run', value });
            },error => {if(controller.worker === worker)this.handleError(controller,error.message);});
        } catch (error) { this.handleError(controller, error.message); }
    }
    handleMessage(controller, message) {
        if (message?.type === 'ready' && controller.phase === 'loading') {
            this.clearRunTimer(controller);
            controller.phase = 'ready';
            controller.status.textContent = 'Plugin worker ready.';
            controller.runButton.disabled = false;
        } else if (message?.type === 'result' && controller.phase === 'running' && Number.isFinite(message.value)) {
            this.clearRunTimer(controller);
            controller.phase = 'ready';
            controller.status.textContent = 'Calculation complete.';
            controller.result.textContent = String(message.value);
            controller.runButton.disabled = false;
        } else if (message?.type === 'error' && typeof message.message === 'string') {
            this.handleError(controller, message.message.slice(0, 2000));
        } else this.handleError(controller, 'Invalid message from plugin worker.');
    }
    handleError(controller, message) {
        this.releaseWorker(controller);
        controller.phase = 'error';
        controller.status.textContent = `Plugin error: ${message || 'Unknown worker failure'}`;
        controller.runButton.disabled = true;
    }
    clearRunTimer(controller) {
        if (controller.runTimer !== null) { this.clearTimer(controller.runTimer); controller.runTimer = null; }
    }
    stop(controller) {
        this.releaseWorker(controller);
        controller.phase = 'stopped';
        for (const [button, callback] of controller.listeners) button.removeEventListener('click', callback);
        if (this.controllers.get(controller.pluginId) === controller) this.controllers.delete(controller.pluginId);
    }
    unmount(root) {
        for (const controller of [...this.controllers.values()]) {
            if (root === controller.element || root?.contains?.(controller.element)) this.stop(controller);
        }
    }
    destroy() { for (const controller of [...this.controllers.values()]) this.stop(controller); }
}
export function bindRuntime(runtime, document, window) {
    const mount = () => runtime.mount(document);
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
    else mount();
    document.body?.addEventListener('htmx:after:settle', mount);
    document.body?.addEventListener('htmx:before:cleanup', event => runtime.unmount(event.target));
    window.addEventListener?.('pagehide', () => runtime.destroy());
    window.addEventListener?.('pageshow', event => { if (event.persisted) mount(); });
}
const runtime = globalThis.document && globalThis.Worker ? new EditorPluginRuntime() : null;
if (runtime) bindRuntime(runtime, globalThis.document, globalThis);
