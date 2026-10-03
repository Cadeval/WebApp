import assert from 'node:assert/strict';
import test from 'node:test';
import * as THREE from 'three';
import { initializeRecording, recenterPreviewModel, fitPreviewGrid, renderPlaybackPosition } from './demo.js';

test('walkthrough progress advances independently of the geometry download progress', () => {
    const download = { value: 100 }, playback = { value: 0 }, label = { textContent: '' };
    const root = { querySelector(selector) { return selector === '[data-demo-playback-progress]' ? playback : selector === '.demo-clock' ? label : download; } };
    renderPlaybackPosition(root, 37);
    assert.equal(playback.value, 37);
    assert.equal(label.textContent, '0:37 / 1:12');
    assert.equal(download.value, 100);
});

test('preview transforms keep geometry intact while centering georeferenced models and fitting the grid', () => {
    const geometry = new THREE.BoxGeometry(20, 12, 15), model = new THREE.Group();
    const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial());
    mesh.position.set(540000, 150, 200000); model.add(mesh);
    const positions = [...geometry.attributes.position.array];
    const bounds = recenterPreviewModel(model);
    assert.ok(bounds.getCenter(new THREE.Vector3()).length() < 1e-8);
    assert.deepEqual([...geometry.attributes.position.array], positions);
    assert.deepEqual(bounds.getSize(new THREE.Vector3()).toArray(), [20, 12, 15]);
    const grid = new THREE.GridHelper(12, 12);
    fitPreviewGrid(grid, bounds);
    assert.equal(grid.scale.x, 20 * 1.35 / 12);
    assert.ok(grid.position.y < bounds.min.y);
    geometry.dispose(); mesh.material.dispose(); grid.geometry.dispose(); grid.material.dispose();
});

function node() {
    const listeners = new Map(), attributes = new Map();
    return { listeners, attributes, hidden: true, textContent: '', disabled: true,
        addEventListener(name, callback) { listeners.set(name, callback); },
        removeEventListener(name, callback) { if (listeners.get(name) === callback) listeners.delete(name); },
        setAttribute(name, value) { attributes.set(name, value); }, removeAttribute(name) { attributes.delete(name); },
        replaceChildren() {},
    };
}

function environment() {
    const nodes = new Map(), body = node();
    const root = { dataset: { recordingUrl: '/recording.json' }, querySelector(selector) { if (!nodes.has(selector)) nodes.set(selector, node()); return nodes.get(selector); } };
    const before = { document: globalThis.document, location: globalThis.location, fetch: globalThis.fetch, cancelAnimationFrame: globalThis.cancelAnimationFrame };
    globalThis.document = { body }; globalThis.location = { href: 'https://example.test/demo' }; globalThis.cancelAnimationFrame = () => {};
    return { root, nodes, body, restore() { for (const [key, value] of Object.entries(before)) { if (value === undefined) delete globalThis[key]; else globalThis[key] = value; } } };
}

test('leaving the demo aborts its recording fetch and clears the loading state', async () => {
    const env = environment();
    let requestedSignal;
    globalThis.fetch = (_, options) => new Promise((_, reject) => {
        requestedSignal = options.signal;
        options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    });
    try {
        const initialized = initializeRecording(env.root);
        assert.equal(env.nodes.get('.demo-scene').attributes.get('aria-busy'), 'true');
        env.body.listeners.get('htmx:before:cleanup')({ target: env.root });
        await initialized;
        assert.equal(requestedSignal.aborted, true);
        assert.equal(env.nodes.get('.demo-scene').attributes.get('aria-busy'), 'false');
        assert.equal(env.nodes.get('[data-demo-loading]').hidden, true);
        assert.equal(env.body.listeners.has('htmx:before:cleanup'), false);
    } finally { env.restore(); }
});

test('leaving a ready demo releases its pending play listener without starting large downloads', async () => {
    const env = environment();
    let fetches = 0;
    globalThis.fetch = async () => { fetches++; return { ok: true, async json() { return { kind: 'prerecorded-controlled-fixture-demo', models: [{ report: 'a.json' }, { report: 'b.json' }], houses: [] }; } }; };
    try {
        const initialized = initializeRecording(env.root);
        // Wait for the small recording document, before visitors start geometry.
        await new Promise(resolve => setTimeout(resolve, 0));
        assert.equal(env.nodes.get('[data-demo-state]').textContent, 'Ready');
        assert.equal(env.nodes.get('[data-demo-play]').listeners.has('click'), true);
        env.body.listeners.get('htmx:before:cleanup')({ target: env.root });
        await initialized;
        assert.equal(fetches, 1);
        assert.equal(env.nodes.get('[data-demo-play]').listeners.has('click'), false);
        assert.equal(env.nodes.get('.demo-scene').attributes.get('aria-busy'), 'false');
    } finally { env.restore(); }
});
