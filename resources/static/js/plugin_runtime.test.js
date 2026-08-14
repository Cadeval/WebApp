import assert from 'node:assert/strict';
import test from 'node:test';

import { EditorPluginRuntime, withReloadVersion } from './plugin_runtime.js';

class FakeControl {
    constructor() {
        this.listeners = new Map();
        this.disabled = false;
        this.value = '21';
        this.textContent = '';
    }

    addEventListener(type, listener) {
        this.listeners.set(type, listener);
    }

    click() {
        this.listeners.get('click')?.({ preventDefault() {} });
    }
}

class FakePluginElement {
    constructor() {
        this.dataset = {
            editorPlugin: 'fake.calculator.editor',
            workerUrl: '/static/js/plugins/fake_calculator_worker.js',
            wasmUrl: '/static/wasm/fake_calculator.wasm',
        };
        this.controls = {
            run: new FakeControl(),
            reload: new FakeControl(),
            input: new FakeControl(),
            status: new FakeControl(),
            result: new FakeControl(),
            generation: new FakeControl(),
        };
    }

    querySelector(selector) {
        const match = selector.match(/data-plugin-(?:action|field)="([^"]+)"/);
        return match ? this.controls[match[1]] : null;
    }
}

class FakeWorker {
    static instances = [];

    constructor(url, options) {
        this.url = url;
        this.options = options;
        this.messages = [];
        this.terminated = false;
        FakeWorker.instances.push(this);
    }

    postMessage(message) {
        this.messages.push(message);
    }

    terminate() {
        this.terminated = true;
    }
}

test('withReloadVersion preserves existing query parameters', () => {
    assert.equal(
        withReloadVersion('/worker.js?mode=test', 3),
        '/worker.js?mode=test&cadevilPluginReload=3',
    );
});

test('editor plugin runtime isolates work and hot reloads its worker', () => {
    FakeWorker.instances = [];
    const plugin = new FakePluginElement();
    const root = { querySelectorAll() { return [plugin]; } };
    const runtime = new EditorPluginRuntime({ WorkerClass: FakeWorker });

    runtime.mount(root);

    assert.equal(FakeWorker.instances.length, 1);
    assert.deepEqual(FakeWorker.instances[0].options, {
        type: 'module',
        name: 'fake.calculator.editor',
    });
    assert.deepEqual(FakeWorker.instances[0].messages[0], {
        type: 'initialize',
        wasmUrl: '/static/wasm/fake_calculator.wasm',
    });

    plugin.controls.run.click();
    assert.deepEqual(FakeWorker.instances[0].messages[1], { type: 'run', value: 21 });

    plugin.controls.reload.click();
    assert.equal(FakeWorker.instances[0].terminated, true);
    assert.equal(FakeWorker.instances.length, 2);
    assert.match(FakeWorker.instances[1].url, /cadevilPluginReload=2$/);

    runtime.destroy();
    assert.equal(FakeWorker.instances[1].terminated, true);
});

test('editor plugin runtime rejects malformed worker messages', () => {
    FakeWorker.instances = [];
    const plugin = new FakePluginElement();
    const root = { querySelectorAll() { return [plugin]; } };
    const runtime = new EditorPluginRuntime({ WorkerClass: FakeWorker });
    runtime.mount(root);

    FakeWorker.instances[0].onmessage({ data: { type: 'result', value: { unsafe: true } } });

    assert.equal(plugin.controls.run.disabled, true);
    assert.match(plugin.controls.status.textContent, /invalid message/i);
});

test('editor plugin runtime terminates workers that exceed their time limit', () => {
    FakeWorker.instances = [];
    const callbacks = [];
    const plugin = new FakePluginElement();
    const root = { querySelectorAll() { return [plugin]; } };
    const runtime = new EditorPluginRuntime({
        WorkerClass: FakeWorker,
        setTimer(callback) { callbacks.push(callback); return callbacks.length; },
        clearTimer() {},
        maxRunMs: 1000,
    });
    runtime.mount(root);

    FakeWorker.instances[0].onmessage({ data: { type: 'ready' } });
    plugin.controls.run.click();
    callbacks.at(-1)();

    assert.equal(FakeWorker.instances[0].terminated, true);
    assert.match(plugin.controls.status.textContent, /timed out/i);
});
