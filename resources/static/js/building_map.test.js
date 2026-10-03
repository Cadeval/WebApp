import assert from 'node:assert/strict';
import test from 'node:test';
import { hasBuildingLocation, groupBuildingsByLocation, filterBuildings, buildingLocationSource,
    safeMapLink, initializeBuildingMap, disposeBuildingMapsWithin } from './building_map.js';

test('locations require finite geographic numbers while accepting the equator and prime meridian', () => {
    assert.equal(hasBuildingLocation({ latitude: 0, longitude: 0 }), true);
    assert.equal(hasBuildingLocation({ latitude: 90, longitude: -180 }), true);
    for (const row of [{ latitude: null, longitude: 0 }, { latitude: '48', longitude: 16 },
        { latitude: Infinity, longitude: 16 }, { latitude: 91, longitude: 16 }, { latitude: 48, longitude: 181 }]) {
        assert.equal(hasBuildingLocation(row), false);
    }
});

test('coincident buildings stay individually selectable even when different uploads share a GUID', () => {
    const first = { id: 'upload-a:same-guid', guid: 'same-guid', latitude: 48.2, longitude: 16.3 };
    const second = { id: 'upload-b:same-guid', guid: 'same-guid', latitude: 48.2, longitude: 16.3 };
    const elsewhere = { id: 'upload-c:same-guid', latitude: 48.21, longitude: 16.3 };
    const missing = { id: 'missing', latitude: null, longitude: null };
    const groups = groupBuildingsByLocation([first, second, elsewhere, missing]);
    assert.equal(groups.length, 2);
    assert.deepEqual(groups[0].buildings.map((row) => row.id), [first.id, second.id]);
    assert.deepEqual(groups[1].buildings, [elsewhere]);
});

test('local search matches all words across building and site without excluding missing locations', () => {
    const rows = [{ id: 'a', title: 'House A', site_name: 'North campus', latitude: null },
        { id: 'b', title: 'House B', site_name: 'South campus', latitude: 48 }];
    assert.deepEqual(filterBuildings(rows, 'ＮＯＲＴＨ house'), [rows[0]]);
    assert.deepEqual(filterBuildings(rows, 'campus'), rows);
    assert.deepEqual(filterBuildings(rows, 'north south'), []);
    assert.equal(filterBuildings(rows, '  '), rows);
});

test('site origins are labelled approximate and missing coordinates never imply a surveyed position', () => {
    assert.equal(buildingLocationSource({ latitude: 48, longitude: 16, source: 'site-reference' }), 'Approximate site origin');
    assert.equal(buildingLocationSource({ latitude: null, longitude: null, source: 'site-reference' }), 'Not located');
    assert.equal(buildingLocationSource({ latitude: 48, longitude: 16, source: 'ifc_site' }), 'Approximate IFC site origin');
    assert.equal(buildingLocationSource({ latitude: 48, longitude: 16, source: 'projected_crs' }), 'Projected IFC location');
    assert.equal(buildingLocationSource({ latitude: 48, longitude: 16, source: 'manual' }), 'User-set location');
    assert.equal(buildingLocationSource({ latitude: 48, longitude: 16, source: '__proto__' }), '__proto__');
});

test('viewer actions only accept same-origin HTTP routes and retain query parameters', () => {
    const origin = 'https://app.example';
    assert.equal(safeMapLink('/plugins/bim/models/a/viewer/?element=guid#part', origin), '/plugins/bim/models/a/viewer/?element=guid#part');
    assert.equal(safeMapLink('https://app.example/overview/1', origin), '/overview/1');
    for (const value of ['javascript:alert(1)', 'data:text/html,hello', 'https://evil.example/a', '//evil.example/a', 'https://user:password@app.example/a', '', null]) {
        assert.equal(safeMapLink(value, origin), null);
    }
});

class Node {
    constructor(document, dataset = {}) {
        this.ownerDocument = document;
        this.dataset = dataset;
        this.children = [];
        this.attributes = new Map();
        this.listeners = new Map();
        this.textContent = '';
        this.hidden = false;
        this.value = '';
        this.checked = true;
        const values = new Set();
        this.classList = { add: (value) => values.add(value), contains: (value) => values.has(value),
            toggle: (value, active) => active ? values.add(value) : values.delete(value) };
    }
    append(...nodes) { for (const node of nodes) { this.children.push(node); node.parent = this; } }
    replaceChildren(...nodes) {
        for (const child of this.children) child.parent = null;
        this.children = [];
        for (const node of nodes) this.append(node);
    }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    removeAttribute(name) { this.attributes.delete(name); }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    addEventListener(name, fn) { if (!this.listeners.has(name)) this.listeners.set(name, new Set()); this.listeners.get(name).add(fn); }
    removeEventListener(name, fn) { this.listeners.get(name)?.delete(fn); }
    emit(name, target = this) { for (const fn of this.listeners.get(name) ?? []) fn({ target }); }
    contains(node) { return node === this || this.children.some((child) => child.contains(node)); }
    querySelector(selector) { return this.selectors?.get(selector) ?? this.querySelectorAll(selector)[0] ?? null; }
    matches(selector) {
        if (selector === '[data-building-thumbnail]') return this.attributes.has('data-building-thumbnail');
        if (selector === 'img[data-thumbnail-src]') return this.attributes.has('data-thumbnail-src');
        if (selector === '[data-thumbnail-state]') return this.attributes.has('data-thumbnail-state');
        return false;
    }
    querySelectorAll(selector) {
        if (selector === '[data-map-building]') return this.rows ?? [];
        return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]);
    }
    closest(selector) { return selector === '[data-map-select]' && this.dataset.mapSelect ? this : null; }
}

function fixture(rows) {
    const state = { processed: [], mapRemoved: 0, observersDisconnected: 0, markerGroups: [], tileHandlers: new Map(),
        tileRedraws: 0, fitCalls: [], sizeInvalidations: 0, layoutFrames: new Map() };
    let frameId = 0;
    const document = { defaultView: { location: { origin: 'https://app.example' }, matchMedia: () => ({ matches: true }),
        htmx: { process: (link) => state.processed.push(link) },
        requestAnimationFrame: (callback) => { state.layoutFrames.set(++frameId, callback); return frameId; },
        cancelAnimationFrame: (identifier) => state.layoutFrames.delete(identifier) }, createElement: () => new Node(document) };
    document.defaultView.ResizeObserver = class {
        constructor(callback) { state.resizeCallback = callback; }
        observe() {}
        disconnect() { state.observersDisconnected += 1; }
    };
    const root = new Node(document, { tileUrl: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png' });
    root.selectors = new Map();
    for (const selector of ['#building-map-data', '[data-map-status]', '[data-map-search]', '[data-map-canvas]',
        '[data-map-selection]', '[data-map-context-host]', '[data-map-fit]', '[data-map-retry]', '[data-map-basemap]', '[data-map-results]',
        '[data-map-no-results]', '[data-map-empty]', '[data-map-selected-title]', '[data-map-selected-site]',
        '[data-map-selected-source]', '[data-map-selected-coordinates]', '[data-map-selected-message]', '[data-map-selected-thumbnail]',
        '[data-map-link="viewer"]', '[data-map-link="overview"]', '[data-map-link="location"]', '[data-map-link="context"]']) {
        const node = new Node(document);
        root.selectors.set(selector, node);
        root.append(node);
    }
    root.querySelector('#building-map-data').textContent = JSON.stringify(rows);
    root.querySelector('[data-map-canvas]').clientWidth = 640;
    root.querySelector('[data-map-canvas]').clientHeight = 420;
    root.rows = rows.map((row) => {
        const node = new Node(document, { mapBuilding: row.id });
        const button = new Node(document, { mapSelect: row.id });
        node.selectors = new Map([['[data-map-select]', button]]);
        node.append(button);
        root.append(node);
        return node;
    });
    const map = { removed: false, fitBounds(bounds, options) { state.fitCalls.push({ bounds, options }); }, panTo() {},
        invalidateSize() { state.sizeInvalidations += 1; }, removeLayer() {},
        remove() { this.removed = true; state.mapRemoved += 1; } };
    const layer = { addTo() { return this; }, clearLayers() { state.markerGroups = []; } };
    const leaflet = {
        map() { state.mapCreated = (state.mapCreated ?? 0) + 1; return map; },
        layerGroup: () => layer,
        latLngBounds: (points) => points,
        control: { scale: () => ({ addTo() {} }) },
        divIcon: (options) => options,
        marker(coordinates, options) {
            const marker = { coordinates, options, element: new Node(document), handlers: new Map(), bindPopup(value) { this.popup = value; return this; },
                getPopup() { return {getContent: () => this.popup}; },
                addTo() { state.markerGroups.push(this); return this; }, on(name, callback) { this.handlers.set(name, callback); return this; },
                getElement() { return this.element; }, openPopup() { this.opened = true; return this; } };
            return marker;
        },
        tileLayer(url, options) {
            state.tileUrl = url;
            state.tileOptions = options;
            return { addTo() { return this; }, on(name, fn) { state.tileHandlers.set(name, fn); return this; },
                off() { state.tileHandlers.clear(); }, redraw() { state.tileRedraws += 1; } };
        },
    };
    return { root, state, leaflet, loadLeaflet: () => Promise.resolve(leaflet) };
}

test('map selection keeps coincident upload identities distinct and processes safe HTMX actions', async () => {
    const rows = [{ id: 'a:guid', title: '<img src=x onerror=alert(1)>', site_name: 'Shared site', latitude: 48, longitude: 16,
        source: 'site-reference', viewer_url: '/viewer/a?element=guid', overview_url: '/overview/a', location_url: '/location/a' },
    { id: 'b:guid', title: 'House B', latitude: 48, longitude: 16, viewer_url: '/viewer/b?element=guid', location_url: '/location/b' }];
    const { root, state, loadLeaflet } = fixture(rows);
    const controller = initializeBuildingMap(root, { loadLeaflet });
    assert.equal(initializeBuildingMap(root, { loadLeaflet }), controller);
    await controller.ready;
    assert.equal(state.mapCreated, 1);
    assert.equal(state.markerGroups.length, 1);
    assert.equal(state.markerGroups[0].options.icon.html, '2');
    assert.equal(state.markerGroups[0].popup.children[1].textContent, rows[0].title);
    controller.selectBuilding('a:guid');
    assert.equal(root.querySelector('[data-map-selected-title]').textContent, rows[0].title);
    assert.equal(root.querySelector('[data-map-link="viewer"]').getAttribute('hx-get'), '/viewer/a?element=guid');
    assert.equal(state.markerGroups[0].element.classList.contains('is-selected'), true);
    controller.selectBuilding('b:guid');
    assert.equal(root.querySelector('[data-map-link="viewer"]').getAttribute('hx-get'), '/viewer/b?element=guid');
    assert.equal(root.querySelector('[data-map-link="overview"]').hidden, true);
    assert.equal(state.processed.length, 5);
    controller.dispose();
});

test('missing locations remain searchable and selection works while the map library is unavailable', async () => {
    const row = { id: 'missing', title: 'House without location', latitude: null, longitude: null,
        viewer_url: '/viewer/missing', location_url: '/location/missing' };
    const { root } = fixture([row]);
    const controller = initializeBuildingMap(root, { loadLeaflet: () => Promise.reject(new Error('offline')) });
    await controller.ready;
    assert.match(root.querySelector('[data-map-status]').textContent, /still open buildings/);
    controller.selectBuilding('missing');
    assert.equal(root.querySelector('[data-map-selected-source]').textContent, 'Not located');
    assert.equal(root.querySelector('[data-map-link="location"]').getAttribute('href'), '/location/missing');
    assert.equal(root.rows[0].hidden, false);
    assert.equal(root.querySelector('[data-map-fit]').disabled, true);
    controller.dispose();
});

test('selected buildings and grouped marker popups use private previews without merging identities', async () => {
    const rows = [{id: 'a:guid', title: '<b>House A</b>', latitude: 48, longitude: 16, thumbnail_url: '/thumbnail/a?building=guid'},
        {id: 'b:guid', title: 'House B', latitude: 48, longitude: 16, thumbnail_url: '/thumbnail/b?building=guid'}];
    const f = fixture(rows), mounted = [], removed = [];
    const controller = initializeBuildingMap(f.root, {loadLeaflet: f.loadLeaflet,
        mountThumbnails: root => mounted.push(root), disposeThumbnails: root => removed.push(root)});
    await controller.ready;
    const marker = f.state.markerGroups[0];
    assert.equal(marker.popup.querySelectorAll('[data-building-thumbnail]').length, 2);
    const popupImages = marker.popup.querySelectorAll('img[data-thumbnail-src]');
    assert.equal(popupImages[0].getAttribute('data-thumbnail-src'), '/thumbnail/a?building=guid');
    assert.equal(popupImages[1].getAttribute('data-thumbnail-src'), '/thumbnail/b?building=guid');
    assert.equal(popupImages[0].getAttribute('alt'), 'Preview of <b>House A</b>');
    marker.handlers.get('popupopen')();
    assert.equal(mounted.at(-1), marker.popup);
    marker.handlers.get('popupclose')();
    assert.equal(removed.at(-1), marker.popup);
    controller.selectBuilding('a:guid');
    const selected = f.root.querySelector('[data-map-selected-thumbnail]');
    const first = selected.children[0];
    assert.equal(selected.hidden, false);
    assert.equal(first.querySelector('img[data-thumbnail-src]').getAttribute('data-thumbnail-src'), rows[0].thumbnail_url);
    controller.selectBuilding('a:guid');
    assert.equal(selected.children[0], first, 'same selection keeps its completed image');
    controller.selectBuilding('b:guid');
    assert.notEqual(selected.children[0], first);
    assert.equal(selected.children[0].querySelector('img[data-thumbnail-src]').getAttribute('data-thumbnail-src'), rows[1].thumbnail_url);
    assert(removed.includes(selected));
    controller.dispose();
    assert(removed.includes(f.root));
});

test('map thumbnails reject external paths while preserving the building selection and viewer links', async () => {
    const f = fixture([{id: 'a', title: 'House A', latitude: 48, longitude: 16,
        thumbnail_url: 'https://evil.example/preview.png', viewer_url: '/viewer/a'}]);
    const controller = initializeBuildingMap(f.root, {loadLeaflet: f.loadLeaflet,
        mountThumbnails() { throw new Error('Unsafe thumbnail mounted'); }, disposeThumbnails() {}});
    await controller.ready;
    assert.equal(f.state.markerGroups[0].popup.querySelectorAll('[data-building-thumbnail]').length, 0);
    controller.selectBuilding('a');
    assert.equal(f.root.querySelector('[data-map-selected-thumbnail]').hidden, true);
    assert.equal(f.root.querySelector('[data-map-link="viewer"]').getAttribute('href'), '/viewer/a');
    controller.dispose();
});

test('lookup targets isolate a late previous-building response and preserve same-building results', async () => {
    const rows = [{ id: 'a:guid', title: 'House A', latitude: 48, longitude: 16, context_url: '/context/a' },
        { id: 'b:guid', title: 'House B', latitude: 48, longitude: 16, context_url: '/context/b' }];
    const { root, loadLeaflet } = fixture(rows);
    const controller = initializeBuildingMap(root, { loadLeaflet });
    const host = root.querySelector('[data-map-context-host]');
    const link = root.querySelector('[data-map-link="context"]');
    controller.selectBuilding('a:guid');
    const previousTarget = host.children[0];
    assert.match(previousTarget.id, /^building-map-context-\d+$/);
    assert.equal(link.getAttribute('href'), '/context/a');
    assert.equal(link.getAttribute('hx-target'), `#${previousTarget.id}`);
    assert.equal(link.getAttribute('hx-swap'), 'outerHTML show:top');
    assert.equal(link.getAttribute('hx-push-url'), 'false');
    controller.selectBuilding('b:guid');
    const currentTarget = host.children[0];
    assert.notEqual(currentTarget.id, previousTarget.id);
    assert.equal(link.getAttribute('hx-get'), '/context/b');
    const lateResult = new Node(root.ownerDocument);
    lateResult.textContent = 'Old House A result';
    previousTarget.replaceChildren(lateResult);
    assert.equal(root.contains(lateResult), false);
    const loaded = new Node(root.ownerDocument);
    loaded.id = currentTarget.id;
    loaded.textContent = 'House B lookup';
    host.replaceChildren(loaded);
    controller.selectBuilding('b:guid');
    assert.equal(host.children[0], loaded);
    await controller.ready; // Leaflet initialization reselects the same building.
    assert.equal(host.children[0], loaded);
    assert.equal(link.getAttribute('hx-target'), `#${loaded.id}`);
    controller.dispose();
    assert.equal(host.children.length, 0);
});

test('lookup links reject external routes and targets remain unique after map remounting', async () => {
    const first = fixture([{ id: 'a', context_url: '/context/a' }]);
    const firstController = initializeBuildingMap(first.root, { loadLeaflet: first.loadLeaflet });
    firstController.selectBuilding('a');
    const firstId = first.root.querySelector('[data-map-context-host]').children[0].id;
    firstController.dispose();
    const second = fixture([{ id: 'a', context_url: '/context/a' }, { id: 'external', context_url: 'https://evil.example/context' }]);
    const secondController = initializeBuildingMap(second.root, { loadLeaflet: second.loadLeaflet });
    secondController.selectBuilding('a');
    assert.notEqual(second.root.querySelector('[data-map-context-host]').children[0].id, firstId);
    secondController.selectBuilding('external');
    assert.equal(second.root.querySelector('[data-map-link="context"]').hidden, true);
    assert.equal(second.root.querySelector('[data-map-context-host]').children.length, 0);
    secondController.dispose();
    await Promise.all([firstController.ready, secondController.ready]);
});

test('street-tile failures leave markers usable and retry clears the stale failure state', async () => {
    const { root, state, loadLeaflet } = fixture([{ id: 'a', title: 'House A', latitude: 48, longitude: 16 }]);
    const controller = initializeBuildingMap(root, { loadLeaflet });
    await controller.ready;
    state.tileHandlers.get('tileerror')();
    assert.match(root.querySelector('[data-map-status]').textContent, /neutral background/);
    assert.equal(root.querySelector('[data-map-retry]').hidden, false);
    assert.equal(state.markerGroups.length, 1);
    root.querySelector('[data-map-retry]').emit('click');
    assert.equal(state.tileRedraws, 1);
    assert.match(root.querySelector('[data-map-status]').textContent, /Loading street map/);
    state.tileHandlers.get('tileload')();
    assert.match(root.querySelector('[data-map-status]').textContent, /Select a marker/);
    root.querySelector('[data-map-basemap]').checked = false;
    root.querySelector('[data-map-basemap]').emit('change');
    assert.match(root.querySelector('[data-map-status]').textContent, /Street map off/);
    controller.dispose();
});

test('street tiles retain the configured provider and supply an origin-only cross-site browser referrer', async () => {
    const { root, state, loadLeaflet } = fixture([{ id: 'a', title: 'House A', latitude: 48, longitude: 16 }]);
    const controller = initializeBuildingMap(root, { loadLeaflet });
    await controller.ready;
    assert.equal(state.tileUrl, 'https://tile.openstreetmap.org/{z}/{x}/{y}.png');
    assert.equal(state.tileOptions.referrerPolicy, 'strict-origin-when-cross-origin');
    assert.match(state.tileOptions.attribution, /https:\/\/www\.openstreetmap\.org\/copyright/);
    controller.dispose();
});

test('initial bounds wait for a real canvas size and later resizes preserve the user zoom', async () => {
    const rows = [{ id: 'a', latitude: 48, longitude: 16 }, { id: 'b', latitude: 48.3, longitude: 16.4 }];
    const { root, state, loadLeaflet } = fixture(rows);
    const canvas = root.querySelector('[data-map-canvas]');
    canvas.clientWidth = 0;
    canvas.clientHeight = 0;
    const controller = initializeBuildingMap(root, { loadLeaflet });
    await controller.ready;
    assert.equal(state.fitCalls.length, 0);
    for (const callback of state.layoutFrames.values()) callback();
    state.layoutFrames.clear();
    state.resizeCallback();
    assert.equal(state.fitCalls.length, 0);
    assert.equal(state.sizeInvalidations, 0);
    canvas.clientWidth = 720;
    canvas.clientHeight = 480;
    state.resizeCallback();
    assert.equal(state.sizeInvalidations, 1);
    assert.equal(state.fitCalls.length, 1);
    assert.deepEqual(state.fitCalls[0].bounds, [[48, 16], [48.3, 16.4]]);
    canvas.clientWidth = 400;
    state.resizeCallback();
    assert.equal(state.sizeInvalidations, 2);
    assert.equal(state.fitCalls.length, 1);
    root.querySelector('[data-map-fit]').emit('click');
    assert.equal(state.fitCalls.length, 2);
    controller.dispose();
});

test('HTMX ancestor cleanup removes the map, observers and handlers exactly once', async () => {
    const { root, state, loadLeaflet } = fixture([{ id: 'a', title: 'House A', latitude: 48, longitude: 16 }]);
    const controller = initializeBuildingMap(root, { loadLeaflet });
    await controller.ready;
    const contentContainer = new Node(root.ownerDocument);
    contentContainer.append(root);
    disposeBuildingMapsWithin(contentContainer);
    controller.dispose();
    assert.equal(state.mapRemoved, 1);
    assert.equal(state.observersDisconnected, 1);
    assert.equal(state.tileHandlers.size, 0);
    assert.equal(root.listeners.get('click').size, 0);
    assert.equal(root.querySelector('[data-map-search]').listeners.get('input').size, 0);
    assert.equal(root.dataset.mapInitialized, undefined);
    assert.equal(state.layoutFrames.size, 0);
});

test('navigation while Leaflet is loading prevents stale map creation after the fragment is removed', async () => {
    const { root, state, leaflet } = fixture([{ id: 'a', latitude: 48, longitude: 16 }]);
    let resolve;
    const pending = new Promise((complete) => { resolve = complete; });
    const controller = initializeBuildingMap(root, { loadLeaflet: () => pending });
    controller.dispose();
    resolve(leaflet);
    await controller.ready;
    assert.equal(state.mapCreated, undefined);
    assert.equal(root.dataset.mapInitialized, undefined);
});
