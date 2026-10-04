import assert from 'node:assert/strict';
import test from 'node:test';

import { extractPartMetrics, formatMetricValue, mountViewerHeader, findIfcMesh } from './3d_view.js';

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
            { label: 'Dimensions (m)', value: '2 × 3 × 4' },
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

test('a still-attached viewer keeps its toolbar available after resource cleanup and remount', () => {
    const next = {}, toolbar = { remove() { assert.fail('Attached BFCache toolbar must be restored'); } };
    const parent = { insertBefore(child, sibling) { assert.equal(child, toolbar); assert.equal(sibling, next); toolbar.parentNode = this; slot.child = null; } };
    toolbar.parentNode = parent; toolbar.nextSibling = next; next.parentNode = parent;
    const slot = { child: null, replaceChildren(child) { this.child = child; toolbar.parentNode = this; }, contains(child) { return this.child === child; } };
    const root = { isConnected: true, querySelector() { return toolbar; } };
    const documentRoot = { getElementById() { return slot; }, body: { classList: { add() {}, remove() {} } } };
    const cleanup = mountViewerHeader(root, documentRoot);
    cleanup();
    assert.equal(toolbar.parentNode, parent);
    const restoredCleanup = mountViewerHeader(root, documentRoot);
    assert.equal(slot.child, toolbar);
    restoredCleanup();
    assert.equal(toolbar.parentNode, parent);
});

test('indexed triangle counts use indices rather than unique vertices', () => {
    const metrics = extractPartMetrics({geometry: {
        index: {count: 36}, attributes: {position: {count: 8}},
    }}).metrics;
    assert.deepEqual(metrics, [{label: 'Triangles', value: '12'}]);
});


test('IFC Name takes precedence over the generated glTF node name', () => {
    assert.equal(extractPartMetrics({userData: {name: 'product-generated', Name: 'Ground slab'}}).name, 'Ground slab');
});

test('error deep links resolve IFC identity on an ancestor of a named mesh',()=>{
 const model={traverse(callback){callback(mesh);}};
 const product={name:'IfcWall',userData:{GlobalId:'offending-wall'},parent:model};
 const mesh={isMesh:true,name:'mesh-with-no-identity',userData:{},parent:product};
 assert.equal(findIfcMesh(model,'offending-wall'),mesh);
 assert.equal(findIfcMesh(model,'missing-wall'),null);
});


test('generated product UUIDs fall back to IFC class while selection retains GUID',()=>{
 const result=extractPartMetrics({name:'product-0c7a9d52-6bf8-46d2-8aac-3812bb5b9c45',userData:{ifcType:'IfcSlab',GlobalId:'stable-guid'}});
 assert.equal(result.name,'IfcSlab');assert.equal(result.guid,'stable-guid');
});
