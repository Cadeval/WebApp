import assert from 'node:assert/strict';
import test from 'node:test';
import {createPluginDetailsController, validDetailHTML} from './plugin_details.js';

class Target {
    constructor() { this.listeners = new Map(); }
    addEventListener(type, listener) { if (!this.listeners.has(type)) this.listeners.set(type, new Set()); this.listeners.get(type).add(listener); }
    removeEventListener(type, listener) { this.listeners.get(type)?.delete(listener); }
    dispatchEvent(event) { event.target ||= this; for (const listener of Array.from(this.listeners.get(event.type) || [])) listener(event); }
}
class Element extends Target {
    constructor(document, tag, attributes = {}) {
        super(); this.ownerDocument = document; this.tagName = tag.toUpperCase(); this.attributes = new Map(Object.entries(attributes));
        this.id = attributes.id; this.children = []; this.isConnected = true; this.hidden = false; this.open = false; this.focusCount = 0; this.clickCount = 0; this.textContent = '';
    }
    matches(selector) { return selector.split(',').some(item => {
        item = item.trim();
        if (item.startsWith('#')) return this.id === item.slice(1);
        const match = item.match(/^([a-z]+)?\[([^\]]+)\]$/);
        return match ? (!match[1] || this.tagName === match[1].toUpperCase()) && this.attributes.has(match[2]) : this.tagName === item.toUpperCase();
    }); }
    closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
    append(...children) { for (const child of children) { this.children.push(child); child.parentElement = this; } }
    querySelectorAll(selector) { return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    contains(node) { return this === node || this.children.some(child => child.contains(node)); }
    hasAttribute(name) { return this.attributes.has(name); }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    focus() { this.focusCount++; this.ownerDocument.activeElement = this; }
    click() { this.clickCount++; }
    showModal() { this.open = true; this.ownerDocument.backgroundInert = true; }
    close() { this.open = false; this.ownerDocument.backgroundInert = false; }
    getBoundingClientRect() { return {left: 500, right: 900, top: 0, bottom: 600}; }
}
function event(type, detail = {}, target = undefined) {
    return {type, detail, target, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }};
}
function fixture() {
    const document = new Target(); document.defaultView = new Target(); document.activeElement = null;
    const root = new Element(document, 'section');
    const dialog = new Element(document, 'dialog', {'data-plugin-detail-dialog': ''}); root.append(dialog);
    const button = new Element(document, 'button', {'data-plugin-detail-close': ''});
    const loading = new Element(document, 'p', {'data-plugin-detail-loading': ''}); loading.hidden = true;
    const body = new Element(document, 'div', {id: 'plugin-detail-body'}); dialog.append(button, loading, body);
    const card = new Element(document, 'article', {'data-plugin-card': ''}); root.append(card);
    const link = new Element(document, 'a', {'data-plugin-details': ''}); link.href = '/plugins/test/details/'; link.textContent = 'Plugin test'; card.append(link);
    const description = new Element(document, 'p'); card.append(description);
    const action = new Element(document, 'button'); card.append(action);
    let navigated;
    function parseHTML(text) {
        const parsed = new Element(document, 'document'); parsed.body = new Element(document, 'body'); parsed.append(parsed.body);
        if (text === 'valid') parsed.body.append(new Element(document, 'div', {'data-plugin-detail-panel': ''}));
        if (text === 'full') parsed.body.append(new Element(document, 'div', {id: 'content-container'}));
        if (text === 'script') { const panel = new Element(document, 'div', {'data-plugin-detail-panel': ''}); parsed.body.append(panel); panel.append(new Element(document, 'script')); }
        if (text === 'multiple') parsed.body.append(new Element(document, 'div', {'data-plugin-detail-panel': ''}), new Element(document, 'div'));
        if (text === 'error') throw Error('Invalid HTML');
        return parsed;
    }
    const controller = createPluginDetailsController(document, {parseHTML, navigate: url => { navigated = url; }});
    function request(source = link) {
        const ctx = {sourceElement: source, target: body, text: 'valid', response: {status: 200}, request: {signal: {aborted: false}, abort() { this.signal.aborted = true; }}};
        document.dispatchEvent(event('htmx:before:request', {ctx})); return ctx;
    }
    return {document, root, dialog, button, body, loading, card, link, description, action, controller, request, parseHTML,
        get navigated() { return navigated; }, emit(type, detail = {}, target) { const evt = event(type, detail, target); document.dispatchEvent(evt); return evt; }};
}

test('drawer HTML accepts its own panel and rejects full pages, scripts and multiple roots', () => {
    const f = fixture();
    assert.equal(validDetailHTML('valid', f.parseHTML), true);
    for (const text of ['full', 'script', 'multiple', 'empty', 'error']) assert.equal(validDetailHTML(text, f.parseHTML), false);
    f.controller.dispose();
});

test('opening uses native modal focus/inert behavior and successful loading stays in the drawer', () => {
    const f = fixture(), ctx = f.request();
    assert.equal(f.dialog.open, true); assert.equal(f.document.backgroundInert, true);
    assert.equal(f.document.activeElement, f.button); assert.equal(f.loading.hidden, false); assert.equal(f.body.getAttribute('aria-busy'), 'true');
    assert.equal(f.emit('htmx:after:request', {ctx}).defaultPrevented, false);
    f.emit('htmx:after:settle', {task: {target: f.body}});
    assert.equal(f.loading.hidden, true); assert.equal(f.body.getAttribute('aria-busy'), 'false');
    f.emit('htmx:finally:request', {ctx}); assert.equal(f.dialog.open, true);
    f.controller.dispose();
});

test('Escape aborts pending work, releases native inert state and restores the opener', () => {
    const f = fixture(), ctx = f.request();
    assert.equal(f.emit('cancel', {}, f.dialog).defaultPrevented, true);
    assert.equal(f.dialog.open, false); assert.equal(f.document.backgroundInert, false);
    assert.equal(ctx.request.signal.aborted, true); assert.equal(f.document.activeElement, f.link);
    assert.equal(f.emit('htmx:after:request', {ctx}).defaultPrevented, true);
    f.emit('htmx:after:settle', {task: {target: f.body}}); assert.equal(f.dialog.open, false);
    f.controller.dispose();
});

test('close button and external native close restore focus without recursion', () => {
    const f = fixture(); f.request();
    f.emit('click', {}, f.button); assert.equal(f.document.activeElement, f.link); assert.equal(f.dialog.open, false);
    f.request(); f.dialog.open = false; f.emit('close', {}, f.dialog);
    assert.equal(f.document.activeElement, f.link); assert.equal(f.link.focusCount, 2);
    f.controller.dispose();
});

test('bad responses preserve the modal and a readable error through finally', () => {
    for (const [status, text] of [[403, 'valid'], [500, 'valid'], [200, 'full'], [200, 'script']]) {
        const f = fixture(), ctx = f.request(); ctx.response.status = status; ctx.text = text;
        assert.equal(f.emit('htmx:after:request', {ctx}).defaultPrevented, true);
        f.emit('htmx:finally:request', {ctx});
        assert.equal(f.dialog.open, true); assert.equal(f.loading.hidden, false);
        assert.match(f.loading.textContent, /could not be loaded/); assert.equal(f.body.getAttribute('aria-busy'), 'false');
        f.controller.dispose();
    }
});

test('stale responses never replace a newer detail request', () => {
    const f = fixture(), old = f.request(), current = f.request();
    assert.equal(f.emit('htmx:after:request', {ctx: old}).defaultPrevented, true);
    f.emit('htmx:finally:request', {ctx: old});
    assert.equal(f.emit('htmx:after:request', {ctx: current}).defaultPrevented, false);
    f.controller.dispose();
});

test('passive workspace cleanup closes only an outgoing ancestor and does not focus the old page', () => {
    const f = fixture(), ctx = f.request();
    f.emit('htmx:before:cleanup', {}, f.action); assert.equal(f.dialog.open, true);
    f.emit('htmx:before:cleanup', {}, f.root);
    assert.equal(f.dialog.open, false); assert.equal(ctx.request.signal.aborted, true); assert.equal(f.link.focusCount, 0);
    assert.equal(f.document.backgroundInert, false); f.controller.dispose();
});

test('history restore, real removal and pagehide release a modal before navigating', () => {
    for (const trigger of ['history', 'history-update', 'removal', 'pagehide']) {
        const f = fixture(); f.request();
        if (trigger === 'history') f.emit('htmx:before:history:restore');
        if (trigger === 'history-update') f.emit('htmx:before:history:update');
        if (trigger === 'removal') { f.dialog.isConnected = false; f.emit('htmx:after:swap'); }
        if (trigger === 'pagehide') f.document.defaultView.dispatchEvent(event('pagehide'));
        assert.equal(f.dialog.open, false); assert.equal(f.document.backgroundInert, false); assert.equal(f.link.focusCount, 0);
        f.controller.dispose();
    }
});

test('unsupported modal API follows the ordinary detail URL without issuing a drawer request', () => {
    const f = fixture(); f.dialog.showModal = undefined;
    const ctx = {sourceElement: f.link, target: f.body};
    assert.equal(f.emit('htmx:before:request', {ctx}).defaultPrevented, true);
    assert.equal(f.navigated, f.link.href); assert.equal(f.dialog.open, false); f.controller.dispose();
});

test('whole tile activation leaves nested controls, links and selected text independent', () => {
    const f = fixture();
    f.emit('click', {}, f.description); assert.equal(f.link.clickCount, 1);
    f.emit('click', {}, f.action); f.emit('click', {}, f.link); assert.equal(f.link.clickCount, 1);
    f.document.defaultView.getSelection = () => ({toString: () => 'selected text'});
    f.emit('click', {}, f.description); assert.equal(f.link.clickCount, 1); f.controller.dispose();
});

test('dialog interior padding does not close; backdrop coordinates do', () => {
    const f = fixture(); f.request();
    let click = event('click', {}, f.dialog); Object.assign(click, {clientX: 600, clientY: 50}); f.document.dispatchEvent(click);
    assert.equal(f.dialog.open, true);
    click = event('click', {}, f.dialog); Object.assign(click, {clientX: 300, clientY: 50}); f.document.dispatchEvent(click);
    assert.equal(f.dialog.open, false); f.controller.dispose();
});

test('disposing removes every listener and closes active native state', () => {
    const f = fixture(); f.request(); f.controller.dispose();
    assert.equal(f.dialog.open, false); assert.equal(f.document.backgroundInert, false);
    assert.equal(Array.from(f.document.listeners.values()).reduce((count, set) => count + set.size, 0), 0);
    assert.equal(Array.from(f.document.defaultView.listeners.values()).reduce((count, set) => count + set.size, 0), 0);
});


test('HTMX4 source swap completion clears successful busy state before request finalization',()=>{
    const f=fixture(),ctx=f.request();f.emit('htmx:after:request',{ctx});
    f.body.append(new Element(f.document,'div',{'data-plugin-detail-panel':''}));
    f.emit('htmx:after:swap',{ctx});f.emit('htmx:finally:request',{ctx});
    assert.equal(f.loading.hidden,true);assert.equal(f.body.getAttribute('aria-busy'),'false');f.controller.dispose();
});
test('swap completion from an aborted or older detail request cannot clear current loading state',()=>{
    const f=fixture(),old=f.request(),current=f.request();
    f.body.append(new Element(f.document,'div',{'data-plugin-detail-panel':''}));
    f.emit('htmx:after:swap',{ctx:old});assert.equal(f.loading.hidden,false);
    current.request.signal.aborted=true;f.emit('htmx:after:swap',{ctx:current});assert.equal(f.loading.hidden,false);f.controller.dispose();
});


test('finalized HTMX4 swapped request clears loading when target events are not delivered',()=>{
    const f=fixture(),ctx=f.request();f.emit('htmx:after:request',{ctx});
    f.body.append(new Element(f.document,'div',{'data-plugin-detail-panel':''}));
    ctx.status='swapped';f.emit('htmx:finally:request',{ctx});
    assert.equal(f.loading.hidden,true);assert.equal(f.body.getAttribute('aria-busy'),'false');f.controller.dispose();
});
