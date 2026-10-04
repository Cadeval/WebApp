import {preverifiedWorker} from './fixtures/verified_worker.js';
import assert from 'node:assert/strict';
import test from 'node:test';
import {createWorkspaceController, nextTabIndex, prepareWorkspaceSwap, validateWorkspaceResponse} from './plugin_workspace.js';
import {EditorPluginRuntime, bindRuntime} from './plugin_runtime.js';

class Target {
    constructor() { this.listeners = new Map(); }
    addEventListener(name, callback) {
        if (!this.listeners.has(name)) this.listeners.set(name, new Set());
        this.listeners.get(name).add(callback);
    }
    removeEventListener(name, callback) { this.listeners.get(name)?.delete(callback); }
    dispatchEvent(event) {
        event.target ||= this;
        for (const callback of Array.from(this.listeners.get(event.type) || [])) callback(event);
        if (event.bubbles) this.parentElement?.dispatchEvent(event);
        return !event.defaultPrevented;
    }
}
class CustomEvent {
    constructor(type, {detail = {}, bubbles = true} = {}) {
        Object.assign(this, {type, detail, bubbles, defaultPrevented: false});
    }
    preventDefault() { this.defaultPrevented = true; }
}
class Element extends Target {
    constructor(doc, tag = 'div', attributes = {}) {
        super(); this.ownerDocument = doc; this.tagName = tag.toUpperCase();
        this.dataset = {}; this.attributes = new Map(); this.children = []; this.focusCount = 0; this.clickCount = 0;
        this.hidden = false; this.textContent = ''; this.isConnected = true;
        for (const [key, value] of Object.entries(attributes)) this.setAttribute(key, value);
    }
    setAttribute(key, value) {
        this.attributes.set(key, String(value));
        if (key === 'id') this.id = String(value);
        if (key.startsWith('data-')) this.dataset[key.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = String(value);
    }
    getAttribute(key) { return this.attributes.get(key) ?? null; }
    hasAttribute(key) { return this.attributes.has(key); }
    removeAttribute(key) { this.attributes.delete(key); }
    matches(selector) {
        if (selector.startsWith('#')) return this.id === selector.slice(1);
        const [, tag, attribute, value] = selector.match(/^([a-z]+)?\[([^=\]]+)(?:="([^"]*)")?\]$/) || [];
        return Boolean(attribute && (!tag || this.tagName === tag.toUpperCase()) && this.attributes.has(attribute)
            && (value === undefined || this.getAttribute(attribute) === value));
    }
    closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest?.(selector) || null; }
    contains(element) { return this === element || this.children.some(child => child.contains(element)); }
    querySelectorAll(selector) { return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    get firstElementChild() { return this.children[0] || null; }
    get childElementCount() { return this.children.length; }
    append(...children) { for (const child of children) { this.children.push(child); child.parentElement = this; } }
    replaceChildren(...children) { this.children = []; this.append(...children); }
    focus(options) { this.focusCount++; this.focusOptions = options; this.ownerDocument.activeElement = this; }
    click() { this.clickCount++; this.dispatchEvent(new CustomEvent('click')); }
}
function fixture() {
    const doc = new Element(null, 'document'); doc.ownerDocument = doc;
    const window = new Target(); window.CustomEvent = CustomEvent; doc.defaultView = window;
    doc.body = new Element(doc, 'body'); doc.append(doc.body); doc.activeElement = doc.body;
    doc.readyState = 'complete'; doc.getElementById = id => doc.querySelector('#' + id);
    const notice = new Element(doc, 'p', {id: 'request-notice'}); notice.hidden = true;
    doc.body.append(notice);
    const content = new Element(doc, 'div', {id: 'content-container'}); doc.body.append(content);
    function workspace(active = 'models') {
        const root = new Element(doc, 'section', {'data-plugin-workspace': 'bim',
            'data-plugin-workspace-url': '/plugins/bim/', 'data-active-tab-id': 'plugin-bim-tab-' + active});
        const nav = new Element(doc, 'nav', {'data-workspace-tablist': ''}); root.append(nav);
        const tabs = ['models', 'map', 'calculate'].map(key => {
            const tab = new Element(doc, 'a', {id: 'plugin-bim-tab-' + key, 'data-workspace-tab': key,
                'aria-controls': 'plugin-bim-panel', href: '/plugins/bim/?tab=' + key});
            if (key === active) tab.setAttribute('aria-current', 'page');
            nav.append(tab); return tab;
        });
        const panel = new Element(doc, 'div', {id: 'plugin-bim-panel', role: 'tabpanel', 'aria-labelledby': root.dataset.activeTabId});
        root.append(panel); return {root, nav, tabs, panel};
    }
    const initial = workspace(); content.append(initial.root);
    const parsedPages = new Map(), parsedTexts = [];
    const parseHTML = text => { parsedTexts.push(text); return parsedPages.get(text); };
    const controller = createWorkspaceController(doc, {parseHTML});
    function responseDocument(text, elements) {
        const parsed = new Element(doc, 'document'); parsed.body = new Element(doc, 'body');
        parsed.append(parsed.body); parsed.body.append(...elements); parsedPages.set(text, parsed); return parsed;
    }
    function emit(type, detail = {}, properties = {}) {
        const event = Object.assign(new CustomEvent(type, {detail}), properties);
        doc.dispatchEvent(event); return event;
    }
    function request(tab = initial.tabs[1]) { return {sourceElement: tab, target: content, swap: 'outerHTML',
        request: {method: 'GET', headers: {}, signal: new AbortController().signal}, response: {status: 200}}; }
    function replace(active) {
        const next = workspace(active); content.replaceChildren(next.root); doc.activeElement = doc.body;
        emit('htmx:after:settle', {task: {target: content}}); return next;
    }
    function swap(ctx = request(), target = content, style = 'outerHTML', valid = true) {
        const replacement = new Element(doc, target === doc.body ? 'body' : 'section', {id: valid ? target.id : 'broken-response'});
        const fragment = {firstElementChild: replacement, childElementCount: 1, querySelector: () => null};
        return {ctx, tasks: [{type: 'main', target, swapSpec: {style}, fragment}]};
    }
    return {doc, window, notice, content, controller, workspace, initial, emit, request, replace, swap,
        responseDocument, parseHTML, parsedTexts};
}

test('tab focus wraps in both directions, respects RTL and supports Home and End', () => {
    assert.equal(nextTabIndex('ArrowLeft', 0, 3), 2);
    assert.equal(nextTabIndex('ArrowRight', 2, 3), 0);
    assert.equal(nextTabIndex('ArrowRight', 0, 3, 'rtl'), 2);
    assert.equal(nextTabIndex('ArrowLeft', 2, 3, 'rtl'), 0);
    assert.equal(nextTabIndex('Home', 2, 3), 0); assert.equal(nextTabIndex('End', 0, 3), 2);
    assert.equal(nextTabIndex('Tab', 0, 3), null); assert.equal(nextTabIndex('Home', 0, 0), null);
});

test('only the server-selected panel becomes active; enhancement never clicks or loads tabs', () => {
    const f = fixture(), {nav, tabs} = f.initial;
    assert.equal(nav.getAttribute('role'), 'tablist');
    assert.deepEqual(tabs.map(tab => tab.getAttribute('aria-selected')), ['true', 'false', 'false']);
    assert.deepEqual(tabs.map(tab => tab.tabIndex), [0, -1, -1]);
    assert(tabs.every(tab => tab.getAttribute('role') === 'tab' && tab.clickCount === 0));
    f.controller.mount(); assert(tabs.every(tab => tab.clickCount === 0)); f.controller.dispose();
});

test('arrow keys move focus manually without changing selection or issuing navigation', () => {
    const f = fixture(), {tabs} = f.initial;
    const event = f.emit('keydown', {}, {target: tabs[0], key: 'ArrowRight'});
    assert(event.defaultPrevented); assert.equal(f.doc.activeElement, tabs[1]);
    assert.deepEqual(tabs.map(tab => tab.tabIndex), [-1, 0, -1]);
    assert.deepEqual(tabs.map(tab => tab.getAttribute('aria-selected')), ['true', 'false', 'false']);
    assert(tabs.every(tab => tab.clickCount === 0));
    f.emit('htmx:after:settle', {task: {target: f.initial.panel}});
    assert.deepEqual(tabs.map(tab => tab.tabIndex), [-1, 0, -1]); f.controller.dispose();
});

test('Enter and Space activate once while editable fields and modified keys remain untouched', () => {
    const f = fixture(), tab = f.initial.tabs[1];
    for (const key of ['Enter', ' ']) assert(f.emit('keydown', {}, {target: tab, key}).defaultPrevented);
    assert.equal(tab.clickCount, 2);
    assert.equal(f.emit('keydown', {}, {target: tab, key: 'ArrowRight', ctrlKey: true}).defaultPrevented, false);
    const input = new Element(f.doc, 'input'); f.initial.panel.append(input);
    assert.equal(f.emit('keydown', {}, {target: input, key: 'ArrowRight'}).defaultPrevented, false);
    f.controller.dispose();
});

test('successful tab navigation restores stable selected-tab focus; rejected requests keep selection', () => {
    const f = fixture(), ctx = f.request();
    f.emit('htmx:before:request', {ctx});
    assert.equal(f.initial.tabs[0].getAttribute('aria-selected'), 'true');
    const next = f.replace('map');
    assert.equal(f.doc.activeElement, next.tabs[1]); assert.deepEqual(next.tabs[1].focusOptions, {preventScroll: true});
    f.emit('htmx:finally:request', {ctx});
    const failed = f.request(next.tabs[2]); f.emit('htmx:before:request', {ctx: failed});
    f.emit('htmx:finally:request', {ctx: failed});
    f.doc.activeElement = f.doc.body; f.emit('htmx:after:settle', {task: {target: f.content}});
    assert.equal(f.doc.activeElement, f.doc.body); assert.equal(next.tabs[1].getAttribute('aria-selected'), 'true');
    f.controller.dispose();
});

test('overlapping requests cannot clear the newer focus intent and inline updates cannot steal focus', () => {
    const f = fixture(), a = f.request(), b = f.request(f.initial.tabs[2]);
    f.emit('htmx:before:request', {ctx: a}); f.emit('htmx:before:request', {ctx: b});
    f.emit('htmx:finally:request', {ctx: a});
    f.emit('htmx:after:settle', {task: {target: f.initial.panel}});
    assert.equal(f.doc.activeElement, f.doc.body);
    const next = f.replace('calculate'); assert.equal(f.doc.activeElement, next.tabs[2]); f.controller.dispose();
});

test('outerHTML settle uses the newly inserted workspace node and completes before the old request finishes', () => {
    const f = fixture(), ctx = f.request(); f.emit('htmx:before:request', {ctx});
    const next = f.workspace('map'); next.root.setAttribute('id', 'content-container');
    f.doc.body.replaceChildren(f.notice, next.root); f.doc.activeElement = f.doc.body;
    f.emit('htmx:after:settle', {task: {target: next.root, sourceElement: ctx.sourceElement}});
    assert.equal(f.doc.activeElement, next.tabs[1]);
    assert.equal(next.tabs[1].getAttribute('aria-selected'), 'true');
    f.emit('htmx:finally:request', {ctx});
    assert.equal(f.doc.activeElement, next.tabs[1]); f.controller.dispose();
});

test('focus moved elsewhere during navigation is preserved', () => {
    const f = fixture(), ctx = f.request(); f.emit('htmx:before:request', {ctx});
    const elsewhere = new Element(f.doc, 'button'); f.doc.body.append(elsewhere); elsewhere.focus();
    const next = f.workspace('map'); f.content.replaceChildren(next.root);
    f.emit('htmx:after:settle', {task: {target: f.content}});
    assert.equal(f.doc.activeElement, elsewhere); assert.equal(next.tabs[1].focusCount, 0); f.controller.dispose();
});

test('history restore focuses the server-selected tab, and failed history requests clear pending focus', () => {
    const f = fixture(); f.emit('htmx:before:history:restore');
    const next = f.replace('calculate'); assert.equal(f.doc.activeElement, next.tabs[2]);
    f.emit('htmx:before:history:restore');
    f.emit('htmx:finally:request', {ctx: {request: {headers: {'HX-History-Restore-Request': 'true'}}}});
    f.doc.activeElement = f.doc.body; f.emit('htmx:after:settle', {task: {target: f.content}});
    assert.equal(f.doc.activeElement, f.doc.body); f.controller.dispose();
});

test('an accepted content replacement dispatches encompassing cleanup once for an unpowered workspace', () => {
    const f = fixture(); let cleaned = 0;
    const viewer = new Element(f.doc, 'section', {id: 'viewer-app'}); f.initial.panel.append(viewer);
    f.doc.body.addEventListener('htmx:before:cleanup', event => { if (event.target.contains(viewer)) cleaned++; });
    assert.equal(f.initial.root._htmx, undefined);
    const detail = f.swap(); f.emit('htmx:before:swap', detail); f.emit('htmx:before:swap', detail);
    assert.equal(cleaned, 1); f.controller.dispose();
});

test('a malformed HTTP 200 page is rejected before history, cleanup or focus can change', () => {
    const f = fixture(), ctx = f.request(); ctx.text = '<p>Unexpected response</p>';
    f.responseDocument(ctx.text, [new Element(f.doc, 'p')]);
    f.initial.tabs[0].focus();
    let historyUpdates = 0, cleanups = 0;
    f.doc.body.addEventListener('htmx:before:cleanup', () => cleanups++);
    f.emit('htmx:before:request', {ctx});
    // HTMX continues to swap()/history only when after:request is accepted.
    const received = f.emit('htmx:after:request', {ctx});
    if (!received.defaultPrevented) {
        historyUpdates++;
        f.emit('htmx:before:swap', f.swap(ctx)); f.replace('map');
    }
    f.emit('htmx:finally:request', {ctx});
    assert(received.defaultPrevented); assert.equal(historyUpdates, 0); assert.equal(cleanups, 0);
    assert.equal(f.doc.activeElement, f.initial.tabs[0]);
    assert.equal(f.initial.tabs[0].getAttribute('aria-selected'), 'true');
    assert.equal(f.initial.tabs[1].getAttribute('aria-selected'), 'false');
    assert.equal(f.notice.hidden, false); assert.deepEqual(f.parsedTexts, [ctx.text]); f.controller.dispose();
});

test('early boundary validation accepts normal pages, OOB navigation updates, full history and explicit selections', () => {
    const f = fixture();
    for (const oobFirst of [false, true]) {
        const text = 'partial-' + oobFirst;
        const boundary = new Element(f.doc, 'section', {id: 'content-container'});
        const oob = new Element(f.doc, 'div', {'hx-swap-oob': 'outerHTML', id: 'plugin-navigation-items'});
        f.responseDocument(text, oobFirst ? [oob, boundary] : [boundary, oob]);
        const ctx = f.request(); ctx.text = text;
        const event = f.emit('htmx:after:request', {ctx}); assert.equal(event.defaultPrevented, false);
    }
    const main = new Element(f.doc, 'main'); main.append(new Element(f.doc, 'div', {id: 'content-container'}));
    const fullPage = '<!doctype html><html><body>Full page</body></html>';
    f.responseDocument(fullPage, [new Element(f.doc, 'header'), main]);
    const history = f.request(); history.target = f.doc.body; history.swap = 'outerSync'; history.text = fullPage;
    assert.equal(f.emit('htmx:after:request', {ctx: history}).defaultPrevented, false);
    const selected = f.request(); selected.select = '#content-container'; selected.text = fullPage;
    assert.equal(f.emit('htmx:after:request', {ctx: selected}).defaultPrevented, false);
    const validation = f.request(); validation.text = 'partial-false'; validation.response.status = 400;
    assert.equal(f.emit('htmx:after:request', {ctx: validation}).defaultPrevented, false); f.controller.dispose();
});

test('early validation rejects duplicates and unselected full pages without parsing inline or non-workspace responses', () => {
    const f = fixture();
    const duplicates = [new Element(f.doc, 'div', {id: 'content-container'}), new Element(f.doc, 'div', {id: 'content-container'})];
    f.responseDocument('duplicate-boundary', duplicates);
    const duplicate = f.request(); duplicate.text = 'duplicate-boundary';
    assert(f.emit('htmx:after:request', {ctx: duplicate}).defaultPrevented);
    const header = new Element(f.doc, 'header'), main = new Element(f.doc, 'main');
    main.append(new Element(f.doc, 'div', {id: 'content-container'})); f.responseDocument('unselected-full-page', [header, main]);
    const full = f.request(); full.text = 'unselected-full-page';
    assert(f.emit('htmx:after:request', {ctx: full}).defaultPrevented);
    const partialHistory = f.request(); partialHistory.target = f.doc.body; partialHistory.swap = 'outerSync';
    partialHistory.text = '<section id="content-container">Partial history response</section>';
    f.responseDocument(partialHistory.text, [new Element(f.doc, 'section', {id: 'content-container'})]);
    assert(f.emit('htmx:after:request', {ctx: partialHistory}).defaultPrevented);
    const parseCount = f.parsedTexts.length;
    const inline = f.request(); inline.target = f.initial.panel; inline.text = 'inline-snippet';
    assert.equal(f.emit('htmx:after:request', {ctx: inline}).defaultPrevented, false);
    const retargeted = f.request(); retargeted.hx = {retarget: '#plugin-bim-panel'};
    assert.equal(f.emit('htmx:after:request', {ctx: retargeted}).defaultPrevented, false);
    const noSwap = f.request(); noSwap.hx = {reswap: 'none'};
    assert.equal(f.emit('htmx:after:request', {ctx: noSwap}).defaultPrevented, false);
    f.content.replaceChildren(new Element(f.doc, 'section', {'data-recorded-demo': ''}));
    assert.equal(f.emit('htmx:after:request', {ctx: f.request()}).defaultPrevented, false);
    assert.equal(f.parsedTexts.length, parseCount); f.controller.dispose();
});

test('early parse failure and cancelled requests preserve outgoing content', () => {
    const f = fixture(), ctx = f.request();
    const event = new CustomEvent('htmx:after:request', {detail: {ctx}});
    assert.equal(validateWorkspaceResponse(event, f.doc, () => { throw new Error('Unreadable HTML'); }), false);
    assert(event.defaultPrevented); assert.equal(f.initial.root.isConnected, true);
    const cancelled = new CustomEvent('htmx:after:request', {detail: {ctx}}); cancelled.preventDefault();
    assert.equal(validateWorkspaceResponse(cancelled, f.doc, assert.fail), false); f.controller.dispose();
});

test('empty-body HTMX redirect, location and refresh headers bypass parsing without cleaning or moving focus', () => {
    const f = fixture(); f.initial.tabs[0].focus(); let cleanups = 0;
    f.doc.body.addEventListener('htmx:before:cleanup', () => cleanups++);
    for (const hx of [{redirect: '/mycelium/login'}, {location: '/plugins/bim/?tab=map'},
        {location: '{path: "/plugins/bim/?tab=map", target: "#content-container"}'}, {refresh: 'true'},
        {redirect: '/mycelium/login', retarget: '#plugin-bim-panel', reswap: 'none'}]) {
        const ctx = f.request(); ctx.hx = hx; ctx.text = '';
        assert.equal(f.emit('htmx:after:request', {ctx}).defaultPrevented, false);
    }
    assert.equal(cleanups, 0); assert.equal(f.doc.activeElement, f.initial.tabs[0]);
    assert.equal(f.notice.hidden, true); assert.equal(f.parsedTexts.length, 0);
    const noRefresh = f.request(); noRefresh.hx = {refresh: 'false'}; noRefresh.text = '';
    assert(f.emit('htmx:after:request', {ctx: noRefresh}).defaultPrevented); f.controller.dispose();
});

test('header navigation cannot bypass aborted requests or unexpected permission/server errors', () => {
    const f = fixture();
    for (const hx of [{redirect: '/mycelium/login'}, {location: '/plugins/bim/'}, {refresh: 'true'}]) {
        for (const status of [403, 404, 500]) {
            const ctx = f.request(); ctx.hx = hx; ctx.text = ''; ctx.response.status = status;
            assert(f.emit('htmx:after:request', {ctx}).defaultPrevented);
        }
        const ctx = f.request(), abort = new AbortController(); abort.abort();
        ctx.hx = hx; ctx.text = ''; ctx.request.signal = abort.signal;
        assert(f.emit('htmx:after:request', {ctx}).defaultPrevented);
    }
    assert.equal(f.parsedTexts.length, 0); f.controller.dispose();
});

test('native 204 and 304 responses preserve content, history intent and focus without a failure notice', () => {
    const f = fixture(); f.initial.tabs[0].focus(); let historyUpdates = 0, cleanups = 0;
    f.doc.body.addEventListener('htmx:before:cleanup', () => cleanups++);
    f.doc.addEventListener('htmx:after:history:update', () => historyUpdates++);
    for (const status of [204, 304]) {
        const ctx = f.request(); ctx.text = ''; ctx.response.status = status;
        ctx.request.method = 'POST'; ctx.push = 'false';
        f.emit('htmx:before:request', {ctx});
        const received = f.emit('htmx:after:request', {ctx}); assert.equal(received.defaultPrevented, false);
        // Native status handling selects swap=none; save forms keep push=false.
        ctx.swap = 'none';
        assert.equal(f.emit('htmx:before:swap', f.swap(ctx, f.content, 'none')).defaultPrevented, false);
        f.emit('htmx:finally:request', {ctx});
        const abort = new AbortController(); abort.abort(); ctx.request.signal = abort.signal;
        assert(f.emit('htmx:after:request', {ctx}).defaultPrevented);
        f.notice.hidden = true;
    }
    assert.equal(historyUpdates, 0); assert.equal(cleanups, 0);
    assert.equal(f.doc.activeElement, f.initial.tabs[0]); assert.equal(f.notice.hidden, true);
    assert.equal(f.parsedTexts.length, 0); f.controller.dispose();
});

test('accepted validation pages are cleaned up; permission/server failures, malformed and cancelled responses are kept', () => {
    for (const status of [200, 400, 409, 422, 403, 404, 500]) {
        const f = fixture(); let count = 0;
        const ctx = f.request(); ctx.response.status = status;
        const event = new CustomEvent('htmx:before:swap', {detail: f.swap(ctx)});
        prepareWorkspaceSwap(event, f.doc, () => count++);
        assert.equal(count, [200, 400, 409, 422].includes(status) ? 1 : 0);
        assert.equal(event.defaultPrevented, status >= 400 && ![400, 409, 422].includes(status)); f.controller.dispose();
    }
    const f = fixture(); let count = 0;
    const malformed = new CustomEvent('htmx:before:swap', {detail: f.swap(f.request(), f.content, 'outerHTML', false)});
    prepareWorkspaceSwap(malformed, f.doc, () => count++);
    assert(malformed.defaultPrevented); assert.equal(count, 0); assert.equal(f.notice.hidden, false);
    const cancelled = new CustomEvent('htmx:before:swap', {detail: f.swap()}); cancelled.preventDefault();
    prepareWorkspaceSwap(cancelled, f.doc, () => count++); assert.equal(count, 0);
    const aborted = f.request(); const abort = new AbortController(); abort.abort(); aborted.request.signal = abort.signal;
    const stale = new CustomEvent('htmx:before:swap', {detail: f.swap(aborted)});
    prepareWorkspaceSwap(stale, f.doc, () => count++); assert(stale.defaultPrevented); assert.equal(count, 0); f.controller.dispose();
});

test('inline swaps and non-replacing updates keep the existing viewer; public demo is outside the scope', () => {
    const f = fixture(); let count = 0;
    for (const [target, style] of [[f.initial.panel, 'innerHTML'], [f.content, 'none'], [f.content, 'beforeend']]) {
        const event = new CustomEvent('htmx:before:swap', {detail: f.swap(f.request(), target, style)});
        prepareWorkspaceSwap(event, f.doc, () => count++); assert.equal(event.defaultPrevented, false);
    }
    const publicDemo = new Element(f.doc, 'section', {'data-recorded-demo': ''});
    f.content.replaceChildren(publicDemo);
    prepareWorkspaceSwap(new CustomEvent('htmx:before:swap', {detail: f.swap()}), f.doc, () => count++);
    assert.equal(count, 0); f.controller.dispose();
});

test('history body replacement disposes the workspace while keeping unrelated popover content outside its cleanup', () => {
    const f = fixture(); let count = 0, logs = 0;
    const popover = new Element(f.doc, 'aside', {id: 'admin-logs-popover'}); f.doc.body.append(popover);
    f.doc.body.addEventListener('htmx:before:cleanup', event => {
        if (event.target.contains(f.initial.root)) count++;
        if (event.target.contains(popover)) logs++;
    });
    f.emit('htmx:before:swap', f.swap(f.request(), f.doc.body, 'outerSync'));
    assert.equal(count, 1); assert.equal(logs, 0); f.controller.dispose();
});

test('cleanup stops the actual plugin runtime worker but a rejected swap leaves it usable', () => {
    const f = fixture(); const instances = [];
    class Worker {
        constructor() { this.terminated = false; instances.push(this); }
        postMessage() {}
        terminate() { this.terminated = true; }
    }
    const card = new Element(f.doc, 'article', {'data-editor-plugin': 'user.plugin', 'data-worker-url': '/plugin.js'});
    for (const [kind, name] of [['action', 'run'], ['action', 'reload'], ['field', 'input'], ['field', 'status'], ['field', 'result'], ['field', 'generation']]) {
        card.append(new Element(f.doc, 'button', {[`data-plugin-${kind}`]: name}));
    }
    f.initial.panel.append(card);
    const runtime = new EditorPluginRuntime({WorkerClass: Worker, prepareWorker:preverifiedWorker, baseUrl: 'https://app.example/',
        setTimer: () => 1, clearTimer() {}, logger: {error: assert.fail}});
    bindRuntime(runtime, f.doc, f.window);
    assert.equal(instances.length, 1);
    const malformed = f.request(); malformed.text = '<p>Malformed page</p>';
    f.responseDocument(malformed.text, [new Element(f.doc, 'p')]);
    assert(f.emit('htmx:after:request', {ctx: malformed}).defaultPrevented);
    assert.equal(instances[0].terminated, false);
    const rejected = f.request(); rejected.response.status = 403;
    f.emit('htmx:before:swap', f.swap(rejected)); assert.equal(instances[0].terminated, false);
    f.emit('htmx:before:swap', f.swap()); assert.equal(instances[0].terminated, true);
    assert.equal(runtime.controllers.size, 0); runtime.destroy(); f.controller.dispose();
});

test('controller disposal removes listeners and BFCache restoration safely restores keyboard semantics', () => {
    const f = fixture(); f.initial.tabs[0].tabIndex = -1;
    f.window.dispatchEvent(Object.assign(new CustomEvent('pageshow'), {persisted: true}));
    assert.equal(f.initial.tabs[0].tabIndex, 0);
    f.controller.dispose();
    const event = f.emit('keydown', {}, {target: f.initial.tabs[0], key: 'ArrowRight'});
    assert.equal(event.defaultPrevented, false);
    assert(Array.from(f.doc.listeners.values()).every(listeners => listeners.size === 0));
});
