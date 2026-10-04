import {preverifiedWorker} from './fixtures/verified_worker.js';
import assert from 'node:assert/strict';
import test from 'node:test';

import { EditorPluginRuntime, withReloadVersion, bindRuntime } from './plugin_runtime.js';

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

    removeEventListener(type, listener) {
        if (this.listeners.get(type) === listener) this.listeners.delete(type);
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
    const runtime = new EditorPluginRuntime({ WorkerClass: FakeWorker, prepareWorker:preverifiedWorker });

    runtime.mount(root);

    assert.equal(FakeWorker.instances.length, 1);
    assert.deepEqual(FakeWorker.instances[0].options, {
        type: 'module',
        name: 'fake.calculator.editor',
    });
    assert.deepEqual(FakeWorker.instances[0].messages[0], {
        type:'cadevil-verified-boot',entry_url:'blob:trusted-fixture',initialize:{type:'initialize',wasmBytes:new ArrayBuffer(8)},
    });

    FakeWorker.instances[0].onmessage({ data: { type: 'ready' } });
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
    const runtime = new EditorPluginRuntime({ WorkerClass: FakeWorker, prepareWorker:preverifiedWorker });
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
        WorkerClass: FakeWorker, prepareWorker:preverifiedWorker,
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

function fixture(options = {}) {
    FakeWorker.instances = [];
    const plugin = new FakePluginElement();
    const timers = new Map(); let id = 0;
    const runtime = new EditorPluginRuntime({WorkerClass: FakeWorker, prepareWorker:preverifiedWorker,
        setTimer(callback) { timers.set(++id,callback); return id; },
        clearTimer(id) { timers.delete(id); }, logger: {}, ...options});
    const root = {querySelectorAll() {return [plugin];}};
    runtime.mount(root);
    return {plugin, runtime, root, timers};
}
test('initialization has a bounded watchdog and releases the worker', () => {
    const {plugin, runtime, timers} = fixture();
    assert.equal(timers.size,1);
    [...timers.values()][0]();
    assert.equal(FakeWorker.instances[0].terminated,true);
    assert.match(plugin.controls.status.textContent,/initialization timed out/i);
    runtime.destroy();
});
test('stale events cannot enable or update the replacement worker', () => {
    const {plugin, runtime} = fixture();
    const stale = FakeWorker.instances[0].onmessage;
    plugin.controls.reload.click();
    stale({data:{type:'ready'}});
    assert.equal(plugin.controls.run.disabled,true);
    FakeWorker.instances[1].onmessage({data:{type:'ready'}});
    assert.equal(plugin.controls.run.disabled,false);
    stale({data:{type:'result',value:999}});
    assert.equal(plugin.controls.result.textContent,'—');
    runtime.destroy();
});
test('worker error terminates it and releases the watchdog', () => {
    const {runtime,timers} = fixture();
    FakeWorker.instances[0].onerror({message:'boom'});
    assert.equal(FakeWorker.instances[0].terminated,true);
    assert.equal(timers.size,0);
    runtime.destroy();
});
test('destroy and remount remove old button listeners', () => {
    const {runtime,root,plugin} = fixture();
    runtime.destroy();
    assert.equal(plugin.controls.reload.listeners.size,0);
    runtime.mount(root);
    plugin.controls.reload.click();
    assert.equal(FakeWorker.instances.length,3);
    runtime.destroy();
});
test('malformed panel cannot prevent another panel from mounting', () => {
    const {runtime,plugin} = fixture();runtime.destroy();
    const bad=new FakePluginElement();bad.dataset.editorPlugin='bad';bad.controls.status=null;
    runtime.mount({querySelectorAll() {return [bad,plugin];}});
    assert.equal(runtime.controllers.size,1);
    assert.ok(runtime.controllers.has(plugin.dataset.editorPlugin));runtime.destroy();
});
test('unrelated HTMX cleanup preserves worker; containing cleanup releases it', () => {
    const {runtime,plugin} = fixture();const worker=FakeWorker.instances[0];
    runtime.unmount({contains() {return false;}});assert.equal(worker.terminated,false);
    runtime.unmount(plugin);assert.equal(worker.terminated,true);
});
test('only one run is dispatched while busy and valid result restores readiness', () => {
    const {runtime,plugin} = fixture();const worker=FakeWorker.instances[0];
    plugin.controls.run.click();assert.equal(worker.messages.length,1);
    worker.onmessage({data:{type:'ready'}});plugin.controls.run.click();plugin.controls.run.click();
    assert.equal(worker.messages.length,2);
    worker.onmessage({data:{type:'result',value:42}});
    assert.equal(plugin.controls.result.textContent,'42');assert.equal(plugin.controls.run.disabled,false);
    runtime.destroy();
});
test('cross-origin worker URL is rejected before creating a worker', () => {
    const {runtime,plugin} = fixture();runtime.destroy();
    plugin.dataset.workerUrl='https://outside.test/worker.js';
    runtime.mount({querySelectorAll(){return [plugin];}});
    assert.equal(FakeWorker.instances.length,1);
    assert.match(plugin.controls.status.textContent,/same-origin/i);runtime.destroy();
});

test('BFCache pagehide releases workers and persisted pageshow remounts', () => {
    const {runtime,plugin,root}=fixture();runtime.destroy();
    const body=new FakeControl();const window=new FakeControl();
    const document={...root,body,readyState:'complete'};
    bindRuntime(runtime,document,window);
    const loaded=FakeWorker.instances.at(-1);
    window.listeners.get('pagehide')({persisted:true});assert.equal(loaded.terminated,true);
    window.listeners.get('pageshow')({persisted:true});
    assert.equal(runtime.controllers.size,1);assert.equal(plugin.controls.reload.listeners.size,1);
    runtime.destroy();
});

const flush=()=>new Promise(resolve=>setImmediate(resolve));
test('async verification creates no worker until its certificate and bytes are verified',async()=>{
    let finish;const f=fixture({prepareWorker:options=>new Promise(resolve=>{finish=()=>resolve(preverifiedWorker(options));})});
    assert.equal(FakeWorker.instances.length,0);assert.equal(f.plugin.controls.run.disabled,true);
    finish();await flush();assert.equal(FakeWorker.instances.length,1);f.runtime.destroy();
});
test('verification rejection creates no executable worker and reports its reason',async()=>{
    const f=fixture({prepareWorker:()=>Promise.reject(Error('Signing certificate was revoked'))});
    await flush();assert.equal(FakeWorker.instances.length,0);assert.match(f.plugin.controls.status.textContent,/revoked/);assert.equal(f.timers.size,0);f.runtime.destroy();
});
test('cleanup aborts pending verification and revokes a late verified result',async()=>{
    let finish,signal,disposed=0;
    const f=fixture({prepareWorker:options=>{signal=options.signal;return new Promise(resolve=>{finish=()=>resolve({...preverifiedWorker(options),dispose(){disposed++;}});});}});
    f.runtime.destroy();assert.equal(signal.aborted,true);finish();await flush();
    assert.equal(FakeWorker.instances.length,0);assert.equal(disposed,1);assert.equal(f.timers.size,0);
});
test('reload rejects a slower verification from the previous generation',async()=>{
    const jobs=[],disposed=[];
    const f=fixture({prepareWorker:options=>new Promise(resolve=>{jobs.push(()=>resolve({...preverifiedWorker(options),dispose(){disposed.push(options.signal.aborted);}}));})});
    f.plugin.controls.reload.click();jobs[0]();await flush();assert.equal(FakeWorker.instances.length,0);assert.deepEqual(disposed,[true]);
    jobs[1]();await flush();assert.equal(FakeWorker.instances.length,1);f.runtime.destroy();
});
test('Run waits for fresh CRL verification and sends no calculation when revocation fails',async()=>{
    let reject;const f=fixture({prepareWorker:options=>({...preverifiedWorker(options),recheck:()=>new Promise((resolve,failed)=>{reject=failed;})})});
    const worker=FakeWorker.instances[0];worker.onmessage({data:{type:'ready'}});f.plugin.controls.run.click();
    assert.equal(worker.messages.length,1);assert.equal(f.plugin.controls.run.disabled,true);
    reject(Error('Fresh CRL revoked the signing certificate'));await flush();assert.equal(worker.terminated,true);assert.equal(worker.messages.length,1);assert.match(f.plugin.controls.status.textContent,/revoked/);f.runtime.destroy();
});
test('cleanup during Run cancels a successful late recheck before it dispatches',async()=>{
    let finish;const f=fixture({prepareWorker:options=>({...preverifiedWorker(options),recheck:()=>new Promise(resolve=>{finish=resolve;})})});
    const worker=FakeWorker.instances[0];worker.onmessage({data:{type:'ready'}});f.plugin.controls.run.click();f.runtime.destroy();finish(true);await flush();
    assert.equal(worker.messages.length,1);assert.equal(worker.terminated,true);
});
test('fresh-trust fetch during Run has a watchdog and cannot leave the user stuck',()=>{
    const f=fixture({prepareWorker:options=>({...preverifiedWorker(options),recheck:()=>new Promise(()=>{})})});
    const worker=FakeWorker.instances[0];worker.onmessage({data:{type:'ready'}});f.plugin.controls.run.click();
    assert.equal(f.timers.size,1);[...f.timers.values()][0]();assert.equal(worker.terminated,true);assert.match(f.plugin.controls.status.textContent,/verification timed out/i);f.runtime.destroy();
});

test('a stale queued verification watchdog cannot stop a new pending generation',()=>{
    const f=fixture({prepareWorker:()=>new Promise(()=>{})});const oldTimer=[...f.timers.values()][0];
    f.plugin.controls.reload.click();oldTimer();assert.equal(f.plugin.controls.run.disabled,true);assert.match(f.plugin.controls.status.textContent,/Verifying plugin certificate/);assert.equal(f.timers.size,1);f.runtime.destroy();
});
