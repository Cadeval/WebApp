import assert from 'node:assert/strict';
import test from 'node:test';
import * as THREE from 'three';

import { extractPartMetrics, formatMetricValue, mountViewerHeader, findIfcMesh, collectSelectableParts, bindViewerKeyboard } from './3d_view.js';

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

test('moving the focused viewer heading into the visible header and back retains focus across remounts', () => {
    const classes = new Set(), body = {classList: {add(value) { classes.add(value); }, remove(value) { classes.delete(value); }}};
    const documentRoot = {body, activeElement: null, getElementById() { return slot; }};
    let focusCount = 0;
    const heading = {isConnected: true, focus(options) { assert.equal(options.preventScroll, true); documentRoot.activeElement = this; focusCount++; }};
    const toolbar = {contains(element) { return element === heading; }, parentNode: null};
    const parent = {insertBefore(child) { assert.equal(child, toolbar); toolbar.parentNode = this; slot.child = null; documentRoot.activeElement = body; }};
    toolbar.parentNode = parent;
    const slot = {child: null, contains(child) { return this.child === child; }, replaceChildren(child) {
        assert(classes.has('viewer-active'), 'The header slot must be visible before restoring focus');
        this.child = child; child.parentNode = this; documentRoot.activeElement = body;
    }};
    const root = {isConnected: true, querySelector() { return toolbar; }};
    documentRoot.activeElement = heading;
    const cleanup = mountViewerHeader(root, documentRoot);
    assert.equal(documentRoot.activeElement, heading);
    assert.equal(focusCount, 1);
    cleanup();
    assert.equal(documentRoot.activeElement, heading);
    assert.equal(focusCount, 2);
    const remountCleanup = mountViewerHeader(root, documentRoot);
    assert.equal(documentRoot.activeElement, heading);
    assert.equal(focusCount, 3);
    remountCleanup();
});

test('viewer header mounting preserves another active control and any newer focus chosen during reparenting', () => {
    const body = {classList: {add() {}, remove() {}}}, otherControl = {};
    const heading = {isConnected: true, focus() { assert.fail('Unrelated or newer focus must not be stolen'); }};
    const toolbar = {contains(element) { return element === heading; }};
    const slot = {replaceChildren() { documentRoot.activeElement = otherControl; }};
    const documentRoot = {body, activeElement: otherControl, getElementById() { return slot; }};
    const root = {querySelector() { return toolbar; }};
    mountViewerHeader(root, documentRoot);
    assert.equal(documentRoot.activeElement, otherControl);
    documentRoot.activeElement = heading;
    mountViewerHeader(root, documentRoot);
    assert.equal(documentRoot.activeElement, otherControl);
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

test('keyboard part choices group IFC products, preserve their labels and exclude hidden surfaces and ancestors', () => {
    const model = new THREE.Group(), wall = new THREE.Group(), hidden = new THREE.Group();
    wall.userData = {GlobalId: 'wall-guid', Name: 'External wall', ifcType: 'IfcWall'};
    wall.add(new THREE.Mesh(), new THREE.Mesh());
    hidden.visible = false;
    const hiddenMesh = new THREE.Mesh(); hiddenMesh.userData = {GlobalId: 'hidden-guid'}; hidden.add(hiddenMesh);
    const space = new THREE.Mesh(); space.userData = {GlobalId: 'space-guid', Name: 'Lobby', ifcType: 'IfcSpace'}; space.visible = false;
    const named = new THREE.Mesh(); named.name = 'Unidentified surface';
    model.add(wall, hidden, space, named);
    let choices = collectSelectableParts(model);
    assert.equal(choices.length, 2);
    assert.equal(choices[0].owner, wall);
    assert.equal(choices[0].mesh, wall.children[0]);
    assert.equal(choices[0].label, 'External wall · IfcWall · wall-guid');
    assert.equal(choices[1].label, 'Unidentified surface · Mesh');
    space.visible = true;
    choices = collectSelectableParts(model);
    assert.equal(choices.length, 3);
    assert.equal(choices[1].guid, 'space-guid');
    assert.equal(choices.some(choice => choice.guid === 'hidden-guid'), false);
});

test('canvas keys zoom within camera limits, invoke fit/clear and release all keyboard controls on cleanup', () => {
    const listeners = new Map();
    const canvas = {addEventListener(name, callback) { listeners.set(name, callback); }, removeEventListener(name, callback) { if (listeners.get(name) === callback) listeners.delete(name); }};
    let fits = 0, clears = 0, updates = 0, stopped = 0;
    const controls = {object: {position: new THREE.Vector3(10, 0, 0)}, target: new THREE.Vector3(), minDistance: 2, maxDistance: 12,
        listenToKeyEvents(target) { assert.equal(target, canvas); }, stopListenToKeyEvents() { stopped++; }, update() { updates++; }};
    const cleanup = bindViewerKeyboard(canvas, controls, {fitModel() { fits++; }, clearSelection() { clears++; }});
    const key = (value, extra = {}) => { let prevented = false; listeners.get('keydown')({key: value, ...extra, preventDefault() { prevented = true; }}); return prevented; };
    assert.equal(key('+'), true);
    assert.equal(controls.object.position.length(), 8);
    for (let count = 0; count < 20; count++) key('+');
    assert.equal(controls.object.position.length(), 2);
    for (let count = 0; count < 20; count++) key('-');
    assert.equal(controls.object.position.length(), 12);
    assert.equal(key('F'), true); assert.equal(fits, 1);
    assert.equal(key('Escape'), true); assert.equal(clears, 1);
    assert.equal(key('f', {ctrlKey: true}), false); assert.equal(fits, 1);
    assert.equal(key('Tab'), false);
    assert(updates > 0);
    cleanup(); assert.equal(stopped, 1); assert.equal(listeners.size, 0);
});
