import assert from 'node:assert/strict';
import test from 'node:test';
import { createGeometryActivity, loadGeometry } from './geometry_loading.js';
import { createActivityController } from './task_activity.js';

function node() {
    const attributes = new Map();
    return { attributes, hidden: true, textContent: '', get value() { return Number(attributes.get('value') || 0); }, set value(value) { attributes.set('value', String(value)); }, getAttribute(key) { return attributes.get(key) ?? null; }, setAttribute(key, value) { attributes.set(key, value); }, removeAttribute(key) { attributes.delete(key); } };
}

test('geometry completion preserves busy state for another task on the same viewport', () => {
    const eventTarget = { addEventListener() {}, removeEventListener() {} };
    const documentRoot = { ...eventTarget, defaultView: eventTarget, getElementById() { return null; } };
    const controller = createActivityController(documentRoot, { setInterval: () => 1, clearInterval: () => {} });
    const target = node(); target.setAttribute('aria-busy', 'false');
    const loading = createGeometryActivity({ target, activity: controller });
    const other = controller.start({ label: 'Another viewport task', target });
    loading.finish();
    assert.equal(target.getAttribute('aria-busy'), 'true');
    controller.finish(other);
    assert.equal(target.getAttribute('aria-busy'), 'false');
    controller.dispose();
});

test('geometry stages distinguish preparation, measured download and processing without guessed percentages', () => {
    const target = node(), overlay = node(), message = node(), detail = node(), progress = node();
    const calls = [];
    const activity = { start(options) { target.setAttribute('aria-busy', 'true'); calls.push(['start', options.label]); return 7; }, update(token, options) { calls.push(['update', token, options.label]); }, finish(token) { target.setAttribute('aria-busy', 'false'); calls.push(['finish', token]); } };
    const loading = createGeometryActivity({ target, overlay, message, detail, progress, activity });
    assert.equal(target.attributes.get('aria-busy'), 'true');
    assert.equal(overlay.hidden, false);
    assert.equal(message.textContent, 'Preparing 3D model…');
    assert.equal(progress.attributes.has('value'), false);
    loading.stage('downloading');
    loading.transfer({ loaded: 5, total: 10 });
    assert.equal(progress.value, 50);
    assert.equal(progress.attributes.get('aria-valuetext'), '50% downloaded');
    loading.transfer({ loaded: 10, total: 10 });
    assert.equal(progress.value, 100);
    assert.equal(overlay.hidden, false, '100% transfer does not mean geometry is renderable');
    loading.stage('processing');
    assert.equal(progress.attributes.has('value'), false);
    assert.equal(progress.attributes.has('aria-valuetext'), false);
    assert.equal(message.textContent, 'Processing 3D model geometry…');
    loading.stage('rendering');
    loading.finish(); loading.finish();
    assert.equal(target.attributes.get('aria-busy'), 'false');
    assert.equal(overlay.hidden, true);
    assert.equal(calls.filter(([kind]) => kind === 'finish').length, 1);
    loading.stage('preparing');
    assert.equal(overlay.hidden, true, 'late callbacks cannot resurrect a disposed loading indicator');
});

test('unknown or mismatched download sizes remain indeterminate', () => {
    const progress = node(), detail = node();
    const loading = createGeometryActivity({ progress, detail, activity: null });
    loading.transfer({ loaded: 2048, total: null });
    assert.equal(progress.attributes.has('value'), false);
    assert.match(detail.textContent, /MB received/);
    loading.transfer({ loaded: 2048, total: 10 });
    assert.equal(progress.attributes.has('value'), false);
    loading.finish();
});

function response(chunks, { length = 6, encoding = null } = {}) {
    let index = 0;
    const reader = {
        async read() { return index < chunks.length ? { done: false, value: chunks[index++] } : { done: true }; },
        released: false, cancelled: false,
        releaseLock() { this.released = true; },
        async cancel() { this.cancelled = true; },
    };
    return {
        ok: true, url: 'https://example.test/models/geometry.glb', status: 200,
        headers: { get(key) { return key === 'content-length' ? String(length) : key === 'content-encoding' ? encoding : null; } },
        body: { getReader() { return reader; } }, reader,
    };
}

test('download bytes, painting opportunity and relative assets are preserved before GLB parsing', async () => {
    const result = response([new Uint8Array([1, 2]), new Uint8Array([3, 4, 5, 6])]);
    const sequence = [], transfers = [];
    const scene = {};
    const gltf = await loadGeometry('https://example.test/models/geometry.glb', {
        fetchImpl: async (_, options) => { assert.equal(options.credentials, 'same-origin'); sequence.push('fetch'); return result; },
        onStage: stage => sequence.push(stage), onProgress: value => transfers.push(value),
        beforeParse: async () => sequence.push('paint'),
        loader: { async parseAsync(buffer, path) { sequence.push('parse'); assert.deepEqual([...new Uint8Array(buffer)], [1, 2, 3, 4, 5, 6]); assert.equal(path, 'https://example.test/models/'); return { scene }; } },
    });
    assert.equal(gltf.scene, scene);
    assert.deepEqual(sequence, ['preparing', 'fetch', 'downloading', 'processing', 'paint', 'parse']);
    assert.deepEqual(transfers, [{ loaded: 2, total: 6 }, { loaded: 6, total: 6 }]);
    assert.equal(result.reader.released, true);
});

test('compressed streams never display a percentage from compressed Content-Length', async () => {
    const result = response([new Uint8Array([1, 2])], { length: 2, encoding: 'gzip' });
    const transfers = [];
    await loadGeometry('https://example.test/geometry.glb', { fetchImpl: async () => result, onProgress: value => transfers.push(value), beforeParse: async () => {}, loader: { async parseAsync() { return {}; } } });
    assert.equal(transfers[0].total, null);
});

test('aborting streamed geometry cancels the reader and prevents parsing', async () => {
    const abort = new AbortController(), result = response([new Uint8Array([1, 2]), new Uint8Array([3, 4])]);
    let parsed = false;
    await assert.rejects(loadGeometry('https://example.test/geometry.glb', {
        signal: abort.signal, fetchImpl: async () => result,
        onProgress: () => abort.abort(), beforeParse: async () => {},
        loader: { async parseAsync() { parsed = true; } },
    }), { name: 'AbortError' });
    assert.equal(parsed, false); assert.equal(result.reader.cancelled, true); assert.equal(result.reader.released, true);
});

test('abort while yielding before processing prevents late parse work', async () => {
    const abort = new AbortController();
    let parsed = false;
    await assert.rejects(loadGeometry('https://example.test/geometry.glb', {
        signal: abort.signal, fetchImpl: async () => response([new Uint8Array([1])]), beforeParse: async () => abort.abort(),
        loader: { async parseAsync() { parsed = true; } },
    }), { name: 'AbortError' });
    assert.equal(parsed, false);
});

test('download and parse failures propagate for the viewer to clear busy and show its error', async () => {
    await assert.rejects(loadGeometry('https://example.test/geometry.glb', { fetchImpl: async () => ({ ok: false, status: 503 }), loader: {} }), /503/);
    await assert.rejects(loadGeometry('https://example.test/geometry.glb', { fetchImpl: async () => response([new Uint8Array([1])]), beforeParse: async () => {}, loader: { async parseAsync() { throw new Error('Invalid geometry'); } } }), /Invalid geometry/);
});
