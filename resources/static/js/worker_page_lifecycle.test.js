import assert from 'node:assert/strict';
import test from 'node:test';
import { bindWorkerPageLifecycle, bindPageResourceLifecycle } from './worker_page_lifecycle.js';
import { IfcEditorRuntime } from './ifc_editor_controller.js';

class EventTarget {
    constructor() { this.listeners = new Map(); }
    addEventListener(type, callback) {
        if (!this.listeners.has(type)) this.listeners.set(type, new Set());
        this.listeners.get(type).add(callback);
    }
    removeEventListener(type, callback) { this.listeners.get(type)?.delete(callback); }
    dispatch(type, event = {}) { for (const callback of this.listeners.get(type) ?? []) callback(event); }
}

test('page resources survive connected swaps but release on real removal or pagehide', () => {
    const document = new EventTarget(), window = new EventTarget(), root = { isConnected: true };
    let releases = 0;
    const dispose = () => { releases++; unbind(); };
    let unbind = bindPageResourceLifecycle(root, dispose, document, window);
    document.dispatch('htmx:after:swap');
    document.dispatch('htmx:before:cleanup', { target: { contains: () => false } });
    assert.equal(releases, 0);
    root.isConnected = false;
    document.dispatch('htmx:after:swap');
    document.dispatch('htmx:after:swap');
    assert.equal(releases, 1);
    root.isConnected = true;
    unbind = bindPageResourceLifecycle(root, dispose, document, window);
    window.dispatch('pagehide');
    assert.equal(releases, 2);
    assert.equal([...document.listeners.values()].every(listeners => listeners.size === 0), true);
    assert.equal([...window.listeners.values()].every(listeners => listeners.size === 0), true);
});

function editor() {
    const root = new EventTarget(), controls = new Map();
    root.isConnected = true;
    root.dataset = { workerUrl: '/static/js/plugins/ifc_editor_worker.js', wasmUrl: '/static/wasm/example_plugin.wasm' };
    root.querySelector = selector => {
        if (!controls.has(selector)) {
            const element = new EventTarget(), body = new EventTarget();
            element.textContent = ''; element.querySelector = name => name === 'tbody' ? body : null;
            controls.set(selector, element);
        }
        return controls.get(selector);
    };
    const timers = new Map(), revoked = [];
    const workers = [];
    class Worker {
        constructor() { this.terminated = false; this.messages = []; workers.push(this); }
        postMessage(message) { this.messages.push(message); }
        terminate() { this.terminated = true; }
    }
    let nextTimer = 0;
    const runtime = new IfcEditorRuntime({ WorkerClass: Worker, setTimer: cb => { timers.set(++nextTimer, cb); return nextTimer; },
        clearTimer: id => timers.delete(id), revokeObjectUrl: url => revoked.push(url) });
    const document = new EventTarget(), window = new EventTarget();
    document.readyState = 'complete';
    document.querySelectorAll = () => root.isConnected ? [root] : [];
    return { runtime, document, window, root, workers, timers, revoked };
}

test('IFC editor passive swaps release workers, operation timers, object URLs and controls', () => {
    const env = editor(), unbind = bindWorkerPageLifecycle(env.runtime, env.document, env.window);
    const controller = env.runtime.controllers.get(env.root);
    env.runtime.postRequest(controller, { type: 'list' });
    controller.activeDownloadUrl = 'blob:editor-output';
    assert.equal(env.timers.size, 1);
    env.document.dispatch('htmx:after:settle');
    assert.equal(env.workers.length, 1, 'Repeated inline updates do not duplicate connected workers');
    env.root.isConnected = false;
    env.document.dispatch('htmx:after:settle');
    assert.equal(env.workers[0].terminated, true);
    assert.equal(env.timers.size, 0);
    assert.deepEqual(env.revoked, ['blob:editor-output']);
    assert.equal(controller.listeners.length, 0);
    assert.equal(env.runtime.controllers.size, 0);
    unbind();
});

test('pagehide destroys workers and persisted pageshow remounts exactly once', () => {
    const env = editor(), unbind = bindWorkerPageLifecycle(env.runtime, env.document, env.window);
    env.window.dispatch('pagehide', { persisted: true });
    assert.equal(env.workers[0].terminated, true);
    env.window.dispatch('pageshow', { persisted: false });
    assert.equal(env.workers.length, 1);
    env.window.dispatch('pageshow', { persisted: true });
    env.document.dispatch('htmx:after:settle');
    assert.equal(env.workers.length, 2);
    assert.equal(env.workers[1].terminated, false);
    unbind();
    assert.equal(env.workers[1].terminated, true);
    assert.equal([...env.document.listeners.values()].every(listeners => listeners.size === 0), true);
    assert.equal([...env.window.listeners.values()].every(listeners => listeners.size === 0), true);
});

test('initial parsing waits for DOMContentLoaded and cleanup stays scoped', () => {
    const env = editor(); env.document.readyState = 'loading';
    const unbind = bindWorkerPageLifecycle(env.runtime, env.document, env.window);
    assert.equal(env.workers.length, 0);
    env.document.dispatch('DOMContentLoaded');
    assert.equal(env.workers.length, 1);
    env.document.dispatch('htmx:before:cleanup', { target: { contains: () => false } });
    assert.equal(env.workers[0].terminated, false);
    env.document.dispatch('htmx:before:cleanup', { target: { contains: root => root === env.root } });
    assert.equal(env.workers[0].terminated, true);
    unbind();
});

test('a slower IFC read cannot replace the most recently selected file', async () => {
    const env = editor(); env.runtime.mount(env.document);
    const controller = env.runtime.controllers.get(env.root);
    let readFirst, readSecond;
    env.runtime.selectFile(controller, { name: 'first.ifc', size: 1, arrayBuffer: () => new Promise(resolve => { readFirst = resolve; }) });
    env.runtime.selectFile(controller, { name: 'second.ifc', size: 1, arrayBuffer: () => new Promise(resolve => { readSecond = resolve; }) });
    const second = new Uint8Array([2]).buffer;
    readSecond(second); await Promise.resolve();
    readFirst(new Uint8Array([1]).buffer); await Promise.resolve();
    assert.equal(controller.selectedFilename, 'second.ifc');
    assert.equal(controller.selectedBytes, second);
    env.runtime.destroy();
});

test('disposed IFC editors ignore late local-file reads and release retained input bytes', async () => {
    const env = editor(); env.runtime.mount(env.document);
    const controller = env.runtime.controllers.get(env.root);
    let finishRead;
    env.runtime.selectFile(controller, { name: 'large.ifc', size: 1, arrayBuffer: () => new Promise(resolve => { finishRead = resolve; }) });
    env.runtime.destroy();
    const status = controller.status.textContent;
    finishRead(new Uint8Array([3]).buffer); await Promise.resolve();
    assert.equal(controller.selectedBytes, null);
    assert.equal(controller.status.textContent, status);
    assert.equal(controller.pending.size, 0);
});
