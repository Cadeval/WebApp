export function withReloadVersion(url, generation) {
    const separator = url.includes('?') ? '&' : '?';
    return `${url}${separator}cadevilPluginReload=${generation}`;
}

function control(element, kind, name) {
    return element.querySelector(`[data-plugin-${kind}="${name}"]`);
}

export class EditorPluginRuntime {
    constructor({
        WorkerClass = globalThis.Worker,
        setTimer = globalThis.setTimeout,
        clearTimer = globalThis.clearTimeout,
        maxRunMs = 2000,
        logger = globalThis.console,
    } = {}) {
        this.WorkerClass = WorkerClass;
        this.setTimer = setTimer;
        this.clearTimer = clearTimer;
        this.maxRunMs = maxRunMs;
        this.logger = logger;
        this.controllers = new Map();
    }

    mount(root = globalThis.document) {
        if (!root?.querySelectorAll || !this.WorkerClass) return;

        const elements = [...root.querySelectorAll('[data-editor-plugin]')];
        const mountedIds = new Set(elements.map((element) => element.dataset.editorPlugin));

        for (const [pluginId, controller] of this.controllers) {
            if (!mountedIds.has(pluginId)) this.stop(controller);
        }

        for (const element of elements) {
            const pluginId = element.dataset.editorPlugin;
            const existing = this.controllers.get(pluginId);
            if (existing?.element === element) continue;
            if (existing) this.stop(existing);

            const controller = {
                pluginId,
                element,
                generation: 0,
                worker: null,
                runTimer: null,
                runButton: control(element, 'action', 'run'),
                reloadButton: control(element, 'action', 'reload'),
                input: control(element, 'field', 'input'),
                status: control(element, 'field', 'status'),
                result: control(element, 'field', 'result'),
                generationLabel: control(element, 'field', 'generation'),
            };
            controller.runButton?.addEventListener('click', () => this.run(controller));
            controller.reloadButton?.addEventListener('click', () => this.start(controller));
            this.controllers.set(pluginId, controller);
            this.start(controller);
        }
    }

    start(controller) {
        controller.worker?.terminate();
        this.clearRunTimer(controller);
        controller.generation += 1;
        controller.runButton.disabled = true;
        controller.status.textContent = 'Loading WebAssembly in worker…';
        controller.result.textContent = '—';
        controller.generationLabel.textContent = `Generation ${controller.generation}`;

        try {
            const workerUrl = withReloadVersion(
                controller.element.dataset.workerUrl,
                controller.generation,
            );
            const worker = new this.WorkerClass(workerUrl, {
                type: 'module',
                name: controller.pluginId,
            });
            controller.worker = worker;
            this.logger?.info?.(`[plugin-manager] Loading plugin '${controller.pluginId}'`);
            worker.onmessage = (event) => this.handleMessage(controller, event.data);
            worker.onerror = (event) => this.handleError(controller, event.message);
            worker.onmessageerror = () => this.handleError(controller, 'Invalid worker message.');
            worker.postMessage({
                type: 'initialize',
                wasmUrl: controller.element.dataset.wasmUrl,
            });
        } catch (error) {
            this.handleError(controller, error.message);
        }
    }

    run(controller) {
        const value = Number(controller.input.value);
        if (!Number.isFinite(value)) {
            this.handleError(controller, 'Enter a finite number.');
            return;
        }
        controller.status.textContent = 'Calculating in isolated worker…';
        controller.worker?.postMessage({ type: 'run', value });
        this.clearRunTimer(controller);
        const configuredLimit = Number(controller.element.dataset.maxRunMs);
        const maxRunMs = Number.isFinite(configuredLimit) && configuredLimit > 0
            ? configuredLimit
            : this.maxRunMs;
        controller.runTimer = this.setTimer(() => {
            controller.worker?.terminate();
            controller.worker = null;
            controller.runTimer = null;
            this.handleError(controller, `Plugin timed out after ${maxRunMs} ms.`);
        }, maxRunMs);
    }

    handleMessage(controller, message) {
        if (!message || typeof message !== 'object' || typeof message.type !== 'string') {
            this.handleError(controller, 'Invalid message from plugin worker.');
        } else if (message.type === 'ready') {
            controller.status.textContent = 'Worker ready; WebAssembly loaded.';
            controller.runButton.disabled = false;
        } else if (message.type === 'result' && Number.isFinite(message.value)) {
            this.clearRunTimer(controller);
            controller.status.textContent = 'Calculation complete.';
            controller.result.textContent = String(message.value);
        } else if (message.type === 'error' && typeof message.message === 'string') {
            this.handleError(controller, message.message);
        } else {
            this.handleError(controller, 'Invalid message from plugin worker.');
        }
    }

    handleError(controller, message) {
        this.clearRunTimer(controller);
        controller.status.textContent = `Plugin error: ${message || 'Unknown worker failure'}`;
        controller.runButton.disabled = true;
    }

    clearRunTimer(controller) {
        if (controller.runTimer !== null) {
            this.clearTimer(controller.runTimer);
            controller.runTimer = null;
        }
    }

    stop(controller) {
        this.clearRunTimer(controller);
        this.logger?.info?.(`[plugin-manager] Unloading plugin '${controller.pluginId}'`);
        controller.worker?.terminate();
        this.controllers.delete(controller.pluginId);
    }

    destroy() {
        for (const controller of [...this.controllers.values()]) this.stop(controller);
    }
}

const runtime = globalThis.document && globalThis.Worker
    ? new EditorPluginRuntime()
    : null;

if (runtime) {
    const mount = () => runtime.mount(globalThis.document);
    globalThis.document.addEventListener('DOMContentLoaded', mount);
    globalThis.document.body?.addEventListener('htmx:afterSettle', mount);
    globalThis.document.body?.addEventListener('htmx:beforeCleanupElement', () => runtime.destroy());
}