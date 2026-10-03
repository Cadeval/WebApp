import assert from 'node:assert/strict';
import test from 'node:test';
import {createThumbnailController, createThumbnailElement, safeThumbnailUrl} from './building_thumbnails.js';

class Element {
    constructor(documentRoot, tag = 'div') {
        this.ownerDocument = documentRoot; this.tagName = tag.toUpperCase();
        this.dataset = {}; this.attributes = new Map(); this.children = [];
        this.hidden = false; this.textContent = '';
    }
    setAttribute(key, value) {
        this.attributes.set(key, String(value));
        if (key === 'data-building-thumbnail') this.thumbnail = true;
    }
    getAttribute(key) { return this.attributes.get(key) ?? null; }
    removeAttribute(key) { this.attributes.delete(key); if (key === 'src') this.src = ''; }
    matches(selector) {
        return selector === '[data-building-thumbnail]' ? Boolean(this.thumbnail)
            : selector === 'img[data-thumbnail-src]' ? this.tagName === 'IMG' && this.attributes.has('data-thumbnail-src')
            : selector === '[data-thumbnail-state]' ? this.attributes.has('data-thumbnail-state') : false;
    }
    append(...elements) { for (const element of elements) { this.children.push(element); element.parent = this; } }
    contains(element) { return element === this || this.children.some(child => child.contains(element)); }
    querySelectorAll(selector) { return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
function fixture({withoutObserver = false} = {}) {
    const state = {requests: [], pending: [], observed: new Set(), revoked: [], urls: [], disconnected: 0};
    const documentRoot = {defaultView: {location: {origin: 'https://app.example'}}};
    documentRoot.createElement = tag => new Element(documentRoot, tag);
    const root = new Element(documentRoot);
    const Observer = withoutObserver ? undefined : class {
        constructor(callback, options) { state.intersect = callback; state.rootMargin = options.rootMargin; }
        observe(element) { state.observed.add(element); }
        unobserve(element) { state.observed.delete(element); }
        disconnect() { state.disconnected++; state.observed.clear(); }
    };
    const controller = createThumbnailController(documentRoot, {
        IntersectionObserver: Observer,
        fetch(url, options) {
            state.requests.push({url, options});
            return new Promise((resolve, reject) => {
                state.pending.push({resolve, reject});
                options.signal.addEventListener('abort', () => reject(new DOMException('Cancelled', 'AbortError')), {once: true});
            });
        },
        createObjectURL(blob) { const url = 'blob:preview-' + state.urls.length; state.urls.push({url, blob}); return url; },
        revokeObjectURL(url) { state.revoked.push(url); },
    });
    function thumbnail(url, label = 'House A') { const host = createThumbnailElement(documentRoot, url, label); root.append(host); return host; }
    function intersect(...hosts) { state.intersect(hosts.map(target => ({target, isIntersecting: true}))); }
    return {documentRoot, root, state, controller, thumbnail, intersect};
}
const flush = async () => { for (let index = 0; index < 8; index++) await Promise.resolve(); };
function response({ok = true, type = 'image/png', size = 12, declared = null} = {}) {
    return {ok, headers: {get(key) { return key === 'content-type' ? type : key === 'content-length' ? declared : null; }},
        async blob() { return new Blob([new Uint8Array(size)], {type}); }};
}

test('private thumbnail and approved public demo paths accept only same-origin HTTP URLs', () => {
    const origin = 'https://app.example';
    assert.equal(safeThumbnailUrl('/plugins/bim/models/a/thumbnail/?building=guid', origin), '/plugins/bim/models/a/thumbnail/?building=guid');
    assert.equal(safeThumbnailUrl('/static/bim-demo/house-a-thumbnail.png', origin), '/static/bim-demo/house-a-thumbnail.png');
    for (const value of ['', null, '//evil.example/a.png', 'https://user@app.example/a.png', 'javascript:alert(1)', 'data:image/png,test', '/preview.png#hash']) {
        assert.equal(safeThumbnailUrl(value, origin), null);
    }
});

test('observing off-screen previews does not request them and intersection caps concurrent work at two', async () => {
    const f = fixture(); const hosts = Array.from({length: 5}, (_, index) => f.thumbnail('/preview/' + index));
    f.controller.mount(f.root);
    assert.equal(f.state.requests.length, 0);
    assert.equal(f.state.observed.size, 5);
    assert.equal(f.state.rootMargin, '200px 0px');
    f.intersect(...hosts);
    assert.equal(f.state.requests.length, 2); assert.equal(f.controller.active, 2);
    f.state.pending[0].resolve(response()); await flush();
    assert.equal(f.state.requests.length, 3); assert.equal(f.controller.active, 2);
    assert.equal(f.state.requests[0].options.credentials, 'same-origin');
    assert.equal(f.state.requests[0].options.redirect, 'error');
    assert.equal(f.state.requests[0].options.headers.Accept, 'image/png');
    f.controller.dispose(); await flush();
});

test('repeated cards share one request and blob until the last consumer leaves', async () => {
    const f = fixture(), first = f.thumbnail('/same-preview'), second = f.thumbnail('/same-preview');
    f.controller.mount(f.root); f.intersect(first, second);
    assert.equal(f.state.requests.length, 1);
    f.state.pending[0].resolve(response()); await flush();
    const firstImage = first.querySelector('img[data-thumbnail-src]'), secondImage = second.querySelector('img[data-thumbnail-src]');
    assert.equal(firstImage.src, secondImage.src); assert.equal(f.state.urls.length, 1);
    const objectUrl = firstImage.src;
    firstImage.onload(); secondImage.onload();
    assert.equal(first.dataset.thumbnailState, 'ready');
    assert.equal(first.querySelector('[data-thumbnail-state]').hidden, true);
    assert.equal(firstImage.hidden, false);
    assert.equal(firstImage.getAttribute('loading'), 'eager');
    f.controller.removeWithin(first);
    assert.equal(f.state.revoked.length, 0);
    assert.equal(second.dataset.thumbnailState, 'ready');
    f.controller.removeWithin(second);
    assert.deepEqual(f.state.revoked, [objectUrl]);
    f.controller.dispose();
});

test('cleanup cancels in-flight work, drops queued records and prevents detached image writes', async () => {
    const f = fixture(), hosts = Array.from({length: 4}, (_, index) => f.thumbnail('/preview/' + index));
    f.controller.mount(f.root); f.intersect(...hosts);
    f.controller.removeWithin(f.root);
    assert.equal(f.controller.size, 0);
    assert.equal(f.state.requests[0].options.signal.aborted, true);
    assert.equal(f.state.requests[1].options.signal.aborted, true);
    f.state.pending[0].resolve(response()); f.state.pending[1].resolve(response()); await flush();
    assert.equal(f.state.requests.length, 2); assert.equal(f.state.urls.length, 0);
    for (const host of hosts) assert.equal(host.querySelector('img[data-thumbnail-src]').hidden, true);
    f.controller.dispose(); assert.equal(f.state.disconnected, 1);
});

test('HTTP, authentication HTML and oversized failures stay local and allow the queue to continue', async () => {
    for (const invalid of [response({ok: false}), response({type: 'text/html'}), response({declared: '3000000'}), response({size: 0})]) {
        const f = fixture(), host = f.thumbnail('/broken'); f.controller.mount(f.root); f.intersect(host);
        f.state.pending[0].resolve(invalid); await flush();
        assert.equal(host.dataset.thumbnailState, 'unavailable');
        assert.equal(host.querySelector('[data-thumbnail-state]').textContent, 'Preview unavailable');
        assert.equal(host.querySelector('img[data-thumbnail-src]').hidden, true);
        assert.equal(f.controller.active, 0); assert.equal(f.state.urls.length, 0);
        f.controller.dispose();
    }
});

test('invalid external paths are unavailable without a request and labels remain plain text', () => {
    const f = fixture(), host = f.thumbnail('https://evil.example/a.png', '<script>Model</script>');
    f.controller.mount(f.root);
    assert.equal(f.state.requests.length, 0);
    assert.equal(host.dataset.thumbnailState, 'unavailable');
    assert.equal(host.querySelector('img[data-thumbnail-src]').getAttribute('alt'), 'Preview of <script>Model</script>');
    f.controller.dispose();
});

test('a local PNG decode error retains an accessible fallback without affecting selection', async () => {
    const f = fixture(), host = f.thumbnail('/preview'); f.controller.mount(f.root); f.intersect(host);
    f.state.pending[0].resolve(response()); await flush();
    host.querySelector('img[data-thumbnail-src]').onerror();
    assert.equal(host.dataset.thumbnailState, 'unavailable');
    assert.equal(host.querySelector('[data-thumbnail-state]').hidden, false);
    f.controller.dispose(); assert.equal(f.state.revoked.length, 1);
});

test('without IntersectionObserver loading still uses the bounded queue and disposal is idempotent', async () => {
    const f = fixture({withoutObserver: true});
    for (let index = 0; index < 5; index++) f.thumbnail('/preview/' + index);
    f.controller.mount(f.root);
    assert.equal(f.state.requests.length, 2);
    f.controller.dispose(); f.controller.dispose(); await flush();
    assert.equal(f.state.requests.length, 2); assert.equal(f.controller.size, 0);
});
