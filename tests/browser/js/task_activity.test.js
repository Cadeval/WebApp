import assert from 'node:assert/strict';
import test from 'node:test';
import { createActivityController, elapsedLabel, requestLabel } from './task_activity.js';

class Element {
    constructor(tag = 'div', parent = null, dataset = {}) {
        this.tagName = tag.toUpperCase(); this.parent = parent; this.dataset = dataset;
        this.attributes = new Map(); this.listeners = new Map(); this.children = [];
        this.textContent = ''; this.hidden = false; this.target = ''; this.isConnected = true;
    }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    removeAttribute(name) { this.attributes.delete(name); }
    addEventListener(name, callback) {
        if (!this.listeners.has(name)) this.listeners.set(name, new Set());
        this.listeners.get(name).add(callback);
    }
    removeEventListener(name, callback) { this.listeners.get(name)?.delete(callback); }
    emit(name, event = {}) {
        event.target ||= this;
        for (const callback of Array.from(this.listeners.get(name) || [])) callback(event);
    }
    matches(selector) {
        return selector === 'form' ? this.tagName === 'FORM'
            : selector === 'form[data-task-label]' ? this.tagName === 'FORM' && Boolean(this.dataset.taskLabel) : false;
    }
    closest(selector) {
        if (selector === '[data-task-label]' && this.dataset.taskLabel) return this;
        if (selector === '[data-download-notice]' && this.dataset.downloadNotice !== undefined) return this;
        if (selector === 'form' && this.tagName === 'FORM') return this;
        return this.parent?.closest(selector) ?? null;
    }
    querySelector(selector) { return this.selectors?.get(selector) ?? null; }
    querySelectorAll() { return this.controls || []; }
    contains(node) { return node === this || Boolean(node?.parent && this.contains(node.parent)); }
    append(...nodes) { for (const node of nodes) { this.children.push(node); node.parent = this; } }
    replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
}

function fixture() {
    const document = new Element();
    const window = new Element(); document.defaultView = window;
    const content = new Element('section');
    const panel = new Element('aside'); panel.hidden = true;
    const status = new Element(); const list = new Element('ol'); const notice = new Element();
    panel.selectors = new Map([['.task-activity-status', status], ['.task-activity-list', list]]);
    document.getElementById = (identifier) => ({ 'task-activity': panel, 'content-container': content, 'request-notice': notice })[identifier] ?? null;
    document.createElement = (tag) => new Element(tag);
    const state = { now: 1000, timers: new Map(), deferred: [], cleared: 0 };
    let nextTimer = 0;
    const controller = createActivityController(document, {
        now: () => state.now,
        setInterval(callback) { state.timers.set(++nextTimer, callback); return nextTimer; },
        clearInterval(timer) { state.timers.delete(timer); state.cleared += 1; },
        defer(callback) { state.deferred.push(callback); },
    });
    const form = new Element('form', content, { taskLabel: 'Calculating material passport' });
    const button = new Element('button', form); button.name = 'action'; button.value = 'calculate';
    button.disabled = false; form.controls = [button];
    function request(source = form, target = content, method = 'POST') {
        return { sourceElement: source, target, request: { method, signal: new AbortController().signal } };
    }
    function event(ctx) { return { detail: { ctx }, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, stopImmediatePropagation() { this.stopped = true; } }; }
    return { document, window, content, panel, status, list, notice, state, controller, form, button, request, event };
}

test('elapsed time is readable and never claims a completion percentage', () => {
    assert.equal(elapsedLabel(-1000), '0s elapsed');
    assert.equal(elapsedLabel(59999), '59s elapsed');
    assert.equal(elapsedLabel(125000), '2m 5s elapsed');
    const form = new Element('form', null, { taskLabel: 'Preparing CityJSON export' });
    const button = new Element('button', form);
    assert.equal(requestLabel({ sourceElement: form, request: { submitter: button } }), 'Preparing CityJSON export');
    assert.equal(requestLabel({ request: { method: 'GET' } }), 'Loading page');
});

test('overlapping requests keep feedback and aria-busy until the final task finishes', () => {
    const f = fixture(); const a = f.request(); const b = f.request(new Element('a', f.content), f.content, 'GET');
    f.content.setAttribute('aria-busy', 'false');
    f.document.emit('htmx:before:request', f.event(a));
    f.document.emit('htmx:before:request', f.event(b));
    assert.equal(f.controller.size, 2); assert.equal(f.panel.hidden, false);
    assert.equal(f.status.textContent, '2 tasks in progress');
    assert.equal(f.content.getAttribute('aria-busy'), 'true');
    f.document.emit('htmx:after:settle', {});
    f.document.emit('htmx:error', { detail: { error: new Error('unrelated script') } });
    assert.equal(f.controller.size, 2);
    f.document.emit('htmx:finally:request', f.event(a));
    assert.equal(f.controller.size, 1); assert.equal(f.content.getAttribute('aria-busy'), 'true');
    f.document.emit('htmx:finally:request', f.event(a));
    assert.equal(f.controller.size, 1);
    f.document.emit('htmx:finally:request', f.event(b));
    assert.equal(f.controller.size, 0); assert.equal(f.panel.hidden, true);
    assert.equal(f.content.getAttribute('aria-busy'), 'false'); assert.equal(f.state.timers.size, 0);
});

test('the final request event on a detached source clears loading after an outerHTML swap', () => {
    const f = fixture(); const ctx = f.request();
    f.document.emit('htmx:before:request', f.event(ctx));
    f.form.isConnected = false; f.form.parent = null;
    f.form.emit('htmx:finally:request', f.event(ctx));
    assert.equal(f.controller.size, 0); assert.equal(f.panel.hidden, true);
    assert.equal(f.button.getAttribute('aria-disabled'), null);
    assert.equal(f.form.listeners.get('htmx:finally:request').size, 0);
});

test('an aborted request clears its own feedback while another request continues', () => {
    const f = fixture(); const aborted = new AbortController();
    const a = f.request(); a.request.signal = aborted.signal;
    const b = f.request(new Element('a', f.content), f.content, 'GET');
    f.document.emit('htmx:before:request', f.event(a)); f.document.emit('htmx:before:request', f.event(b));
    aborted.abort();
    assert.equal(f.controller.size, 1); assert.equal(f.panel.hidden, false);
    f.form.emit('htmx:finally:request', f.event(a));
    assert.equal(f.controller.size, 1);
    f.document.emit('htmx:finally:request', f.event(b));
    assert.equal(f.controller.size, 0);
});

test('server and network errors clear when HTMX completes the failed request', () => {
    for (const response of [{ status: 500 }, null]) {
        const f = fixture(); const ctx = f.request();
        f.document.emit('htmx:before:request', f.event(ctx));
        ctx.response = response;
        f.document.emit(response ? 'htmx:response:error' : 'htmx:error', f.event(ctx));
        f.form.emit('htmx:finally:request', f.event(ctx));
        assert.equal(f.controller.size, 0);
        assert.equal(f.content.getAttribute('aria-busy'), null);
    }
});

test('repeat form submission is prevented without disabling serialized fields or submitter values', () => {
    const f = fixture(); const ctx = f.request();
    f.document.emit('htmx:before:request', f.event(ctx));
    assert.equal(f.button.disabled, false); assert.equal(f.button.name, 'action'); assert.equal(f.button.value, 'calculate');
    assert.equal(f.button.getAttribute('aria-disabled'), 'true');
    const submit = f.event(); submit.target = f.form;
    f.document.emit('submit', submit);
    assert.equal(submit.defaultPrevented, true); assert.equal(submit.stopped, true);
    const config = f.event(f.request()); f.document.emit('htmx:config:request', config);
    assert.equal(config.defaultPrevented, true);
    f.form.emit('htmx:finally:request', f.event(ctx));
    const retry = f.event(f.request()); f.document.emit('htmx:config:request', retry);
    assert.equal(retry.defaultPrevented, false);
});

test('canceled requests and unrelated background tasks do not create indicators', () => {
    const f = fixture(); const cancelled = f.event(f.request()); cancelled.defaultPrevented = true;
    f.document.emit('htmx:before:request', cancelled);
    const external = new Element();
    f.document.emit('htmx:before:request', f.event(f.request(external, external, 'GET')));
    assert.equal(f.controller.size, 0);
    const nested = new Element('section', f.content);
    f.document.emit('htmx:before:request', f.event(f.request(new Element('a', nested), nested, 'GET')));
    assert.equal(f.controller.size, 1);
    f.controller.dispose();
});

test('an annotated lookup outside main content receives its own task label', () => {
    const f = fixture(); const anchor = new Element('a', null, { taskLabel: 'Looking up local prices and building rules' });
    const target = new Element();
    const ctx = f.request(anchor, target, 'GET');
    f.document.emit('htmx:before:request', f.event(ctx));
    assert.equal(f.status.textContent, anchor.dataset.taskLabel);
    assert.equal(target.getAttribute('aria-busy'), 'true');
    f.state.now += 65000;
    for (const callback of f.state.timers.values()) callback();
    assert.equal(f.list.children[0].children[2].textContent, '1m 5s elapsed');
    assert.equal(f.status.textContent, anchor.dataset.taskLabel);
    anchor.emit('htmx:finally:request', f.event(ctx));
});

test('manual tasks update phases, preserve existing accessibility state and finish idempotently', () => {
    const f = fixture(); f.content.setAttribute('aria-busy', 'true');
    const a = f.controller.start({ label: 'Preparing 3D model', target: f.content });
    const b = f.controller.start({ label: 'Loading material properties', target: f.content });
    f.controller.update(a, { label: 'Rendering 3D model' });
    assert.equal(f.list.children[0].children[1].textContent, 'Rendering 3D model');
    f.controller.finish(a); f.controller.finish(a);
    assert.equal(f.controller.size, 1);
    f.controller.finish(b);
    assert.equal(f.content.getAttribute('aria-busy'), 'true');
});

test('native form fallback starts after event dispatch and resets on browser history return', () => {
    const f = fixture(); const native = f.event(); native.target = f.form;
    f.document.emit('submit', native);
    assert.equal(f.controller.size, 0);
    f.state.deferred.shift()();
    assert.equal(f.controller.size, 1); assert.equal(f.form.getAttribute('aria-busy'), 'true');
    f.window.emit('pageshow', { persisted: true });
    assert.equal(f.controller.size, 0); assert.equal(f.form.getAttribute('aria-busy'), null);
    const intercepted = f.event(); intercepted.target = f.form;
    f.document.emit('submit', intercepted); intercepted.defaultPrevented = true;
    f.state.deferred.shift()();
    assert.equal(f.controller.size, 0);
});

test('native downloads explain the browser handoff without an invented completion timer', () => {
    const f = fixture(); const link = new Element('a', null, { downloadNotice: '' });
    f.document.emit('click', { target: link, button: 0 });
    assert.match(f.notice.textContent, /browser will handle this download/);
    assert.equal(f.notice.hidden, false); assert.equal(f.controller.size, 0);
    f.notice.textContent = '';
    f.document.emit('click', { target: link, button: 0, ctrlKey: true });
    assert.equal(f.notice.textContent, '');
});

test('disposing clears task timers, busy forms and listeners', () => {
    const f = fixture(); f.document.emit('htmx:before:request', f.event(f.request()));
    f.controller.dispose();
    assert.equal(f.controller.size, 0); assert.equal(f.state.timers.size, 0);
    assert.equal(f.form.getAttribute('data-task-running'), null);
    assert.equal(f.document.listeners.get('htmx:before:request').size, 0);
});

test('versioned and unversioned imports share one global controller and one set of listeners', async () => {
    const f = fixture();
    f.controller.dispose();
    f.window.setInterval = () => 1; f.window.clearInterval = () => {};
    globalThis.window = f.window; globalThis.document = f.document;
    try {
        const first = await import('./task_activity.js?singleton-test=one');
        const second = await import('./task_activity.js?singleton-test=two');
        assert.equal(f.document.listeners.get('htmx:before:request').size, 1);
        const token = first.startTask({ label: 'Loading 3D model', target: f.content });
        assert.equal(f.panel.hidden, false);
        second.finishTask(token);
        assert.equal(f.panel.hidden, true);
        f.window[Symbol.for('cadevil.taskActivity')].dispose();
    } finally {
        delete globalThis.window; delete globalThis.document;
    }
});
