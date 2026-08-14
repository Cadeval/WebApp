import assert from 'node:assert/strict';
import test from 'node:test';

import { extractPartMetrics, formatMetricValue, mountViewerHeader } from './3d_view.js';

test('formatMetricValue formats supported values and rejects unusable values', () => {
    assert.equal(formatMetricValue(12.3456), '12.35');
    assert.equal(formatMetricValue(true), 'Yes');
    assert.equal(formatMetricValue(false), 'No');
    assert.equal(formatMetricValue('Concrete'), 'Concrete');
    assert.equal(formatMetricValue(Number.POSITIVE_INFINITY), '—');
    assert.equal(formatMetricValue(null), '—');
});

test('extractPartMetrics combines IFC metadata with geometry measurements', () => {
    const part = {
        name: 'Wall 01',
        userData: {
            GlobalId: '2wB8Xf',
            ifcType: 'IfcWall',
            Area: 12.3456,
            IsExternal: true,
            internalObject: { hidden: true },
        },
        geometry: {
            attributes: { position: { count: 12 } },
            computeBoundingBox() {},
            boundingBox: {
                min: { x: 1, y: 2, z: 3 },
                max: { x: 3, y: 5, z: 7 },
            },
        },
    };

    assert.deepEqual(extractPartMetrics(part), {
        name: 'Wall 01',
        type: 'IfcWall',
        guid: '2wB8Xf',
        metrics: [
            { label: 'Area', value: '12.35' },
            { label: 'Is External', value: 'Yes' },
            { label: 'Dimensions', value: '2 × 3 × 4' },
            { label: 'Triangles', value: '4' },
        ],
    });
});

test('extractPartMetrics provides safe fallbacks for unnamed parts without geometry', () => {
    assert.deepEqual(extractPartMetrics({ userData: {} }), {
        name: 'Unnamed building part',
        type: 'Mesh',
        guid: 'Not available',
        metrics: [],
    });
});

test('mountViewerHeader moves viewer controls into the application header and cleans up', () => {
    const toolbar = { removeCalled: false, remove() { this.removeCalled = true; } };
    const slot = {
        child: null,
        replaceChildren(child) { this.child = child; },
        contains(child) { return this.child === child; },
    };
    const classes = new Set();
    const documentRoot = {
        body: {
            classList: {
                add(value) { classes.add(value); },
                remove(value) { classes.delete(value); },
            },
        },
        getElementById(id) { return id === 'viewer-header-slot' ? slot : null; },
    };
    const root = {
        querySelector(selector) {
            return selector === '[data-viewer-header-content]' ? toolbar : null;
        },
    };

    const cleanup = mountViewerHeader(root, documentRoot);

    assert.equal(slot.child, toolbar);
    assert.equal(classes.has('viewer-active'), true);
    cleanup();
    assert.equal(toolbar.removeCalled, true);
    assert.equal(classes.has('viewer-active'), false);
});

test('mountViewerHeader is a safe no-op without an application header slot', () => {
    const cleanup = mountViewerHeader(
        { querySelector() { return {}; } },
        { getElementById() { return null; } },
    );

    assert.equal(typeof cleanup, 'function');
    assert.doesNotThrow(cleanup);
});