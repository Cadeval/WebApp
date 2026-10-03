const controllers = new Map();
let leafletImport;
let contextTargetCounter = 0;

export function hasBuildingLocation(building) {
    return typeof building?.latitude === 'number' && Number.isFinite(building.latitude)
        && typeof building?.longitude === 'number' && Number.isFinite(building.longitude)
        && Math.abs(building.latitude) <= 90 && Math.abs(building.longitude) <= 180;
}

export function groupBuildingsByLocation(buildings) {
    const groups = new Map();
    for (const building of buildings) {
        if (!hasBuildingLocation(building)) continue;
        const key = `${building.latitude.toFixed(6)},${building.longitude.toFixed(6)}`;
        if (!groups.has(key)) groups.set(key, { latitude: building.latitude, longitude: building.longitude, buildings: [] });
        groups.get(key).buildings.push(building);
    }
    return [...groups.values()];
}

export function filterBuildings(buildings, query) {
    const words = String(query ?? '').normalize('NFKC').toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
    if (!words.length) return buildings;
    return buildings.filter((building) => {
        const text = [building.title, building.site_name, building.source, building.guid].join(' ').normalize('NFKC').toLocaleLowerCase();
        return words.every((word) => text.includes(word));
    });
}

export function buildingLocationSource(building) {
    if (!hasBuildingLocation(building)) return 'Not located';
    const sources = { 'site-reference': 'Approximate site origin', ifc_site: 'Approximate IFC site origin',
        projected_crs: 'Projected IFC location', manual: 'User-set location',
        'ifc-site': 'IFC site location', 'ifc-georeference': 'IFC georeference', cityjson: 'CityJSON geometry',
        cityjson_geometry: 'CityJSON building geometry' };
    return Object.hasOwn(sources, building.source) ? sources[building.source] : String(building.source || 'Source location');
}

export function safeMapLink(value, origin) {
    if (typeof value !== 'string' || !value.trim()) return null;
    try {
        const url = new URL(value, origin);
        if (url.origin !== origin || !['http:', 'https:'].includes(url.protocol) || url.username || url.password) return null;
        return url.pathname + url.search + url.hash;
    } catch { return null; }
}

function readBuildings(root) {
    const value = JSON.parse(root.querySelector('#building-map-data')?.textContent ?? '[]');
    if (!Array.isArray(value)) throw new Error('The building locations are not a list.');
    return value.filter((row) => row && typeof row.id === 'string');
}

function setStatus(node, text) {
    if (node && node.textContent !== text) node.textContent = text;
}

/** Local Leaflet keeps the building layer usable without an external basemap. */
export function initializeBuildingMap(root, dependencies = {}) {
    if (!root || controllers.has(root)) return controllers.get(root) ?? null;
    const documentRoot = root.ownerDocument;
    const windowRoot = documentRoot.defaultView;
    const status = root.querySelector('[data-map-status]');
    const search = root.querySelector('[data-map-search]');
    const canvas = root.querySelector('[data-map-canvas]');
    const selection = root.querySelector('[data-map-selection]');
    const contextHost = root.querySelector('[data-map-context-host]');
    const fitButton = root.querySelector('[data-map-fit]');
    const retryButton = root.querySelector('[data-map-retry]');
    const basemapToggle = root.querySelector('[data-map-basemap]');
    let buildings;
    try { buildings = readBuildings(root); } catch {
        setStatus(status, 'Building locations could not be read. Use the building list to open models.');
        return null;
    }
    const byId = new Map(buildings.map((building) => [building.id, building]));
    const listRows = [...root.querySelectorAll('[data-map-building]')];
    let visible = buildings;
    let selected = null;
    let contextTargetId = null;
    let map = null;
    let markerLayer = null;
    let tiles = null;
    let L = null;
    let disposed = false;
    let resizeObserver = null;
    let layoutFrame = null;
    let initialFitPending = true;
    let tileFailures = 0;
    let tileSuccesses = 0;
    const markersById = new Map();

    function selectBuilding(identifier) {
        const building = byId.get(identifier);
        if (!building || disposed) return;
        const changed = selected !== identifier;
        selected = identifier;
        const contextRoute = safeMapLink(building.context_url, windowRoot.location.origin);
        if (contextHost && (changed || !contextTargetId)) {
            contextHost.replaceChildren();
            contextTargetId = null;
            if (contextRoute) {
                const target = documentRoot.createElement('div');
                target.id = `building-map-context-${++contextTargetCounter}`;
                contextTargetId = target.id;
                contextHost.append(target);
            }
        }
        selection.hidden = false;
        root.querySelector('[data-map-selected-title]').textContent = building.title || 'Unnamed building';
        root.querySelector('[data-map-selected-site]').textContent = building.site_name || 'Site not specified';
        root.querySelector('[data-map-selected-source]').textContent = buildingLocationSource(building);
        root.querySelector('[data-map-selected-coordinates]').textContent = hasBuildingLocation(building)
            ? `${building.latitude.toFixed(6)}, ${building.longitude.toFixed(6)}` : 'Not available';
        root.querySelector('[data-map-selected-message]').textContent = building.message
            || (['site-reference', 'ifc_site'].includes(building.source) ? 'The IFC supplies a site origin. Set a building location for a more precise marker.'
                : hasBuildingLocation(building) ? '' : 'Set a location to place this building on the map.');
        for (const [kind, value] of [['viewer', building.viewer_url], ['overview', building.overview_url],
            ['location', building.location_url], ['context', building.context_url]]) {
            const link = root.querySelector(`[data-map-link="${kind}"]`);
            if (!link) continue;
            const route = safeMapLink(value, windowRoot.location.origin);
            link.hidden = !route;
            if (route) {
                link.setAttribute('href', route);
                link.setAttribute('hx-get', route);
                if (kind === 'context') {
                    link.setAttribute('hx-target', `#${contextTargetId}`);
                    link.setAttribute('hx-swap', 'outerHTML show:top');
                    link.setAttribute('hx-push-url', 'false');
                }
                windowRoot.htmx?.process?.(link);
            } else {
                link.removeAttribute('href');
                link.removeAttribute('hx-get');
            }
        }
        for (const row of listRows) {
            const active = row.dataset.mapBuilding === identifier;
            row.classList.toggle('is-selected', active);
            row.querySelector('[data-map-select]')?.setAttribute('aria-pressed', String(active));
        }
        const selectedMarker = markersById.get(selected);
        for (const marker of new Set(markersById.values())) marker.getElement()?.classList.toggle('is-selected', marker === selectedMarker);
        if (map && hasBuildingLocation(building)) {
            map.panTo([building.latitude, building.longitude], { animate: !windowRoot.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches });
            markersById.get(identifier)?.openPopup();
        }
    }

    function popupFor(group) {
        const popup = documentRoot.createElement('div');
        popup.className = 'building-map-popup';
        const heading = documentRoot.createElement('strong');
        heading.textContent = group.buildings.length === 1 ? 'Building at this location' : `${group.buildings.length} buildings at this location`;
        popup.append(heading);
        for (const building of group.buildings) {
            const button = documentRoot.createElement('button');
            button.type = 'button';
            button.textContent = building.title || 'Unnamed building';
            button.addEventListener('click', () => selectBuilding(building.id));
            popup.append(button);
        }
        return popup;
    }

    function refreshMarkers() {
        if (!map) return;
        markerLayer.clearLayers();
        markersById.clear();
        for (const group of groupBuildingsByLocation(visible)) {
            const count = group.buildings.length;
            const label = count === 1 ? group.buildings[0].title : `${count} buildings at this location`;
            const marker = L.marker([group.latitude, group.longitude], {
                title: label, alt: label, keyboard: true,
                icon: L.divIcon({ className: 'building-map-marker', html: String(count), iconSize: [34, 34], iconAnchor: [17, 17] }),
            }).bindPopup(popupFor(group), { maxWidth: 300 }).addTo(markerLayer);
            marker.on('click', () => { if (count === 1) selectBuilding(group.buildings[0].id); });
            for (const building of group.buildings) markersById.set(building.id, marker);
            if (group.buildings.some((building) => building.id === selected)) marker.getElement()?.classList.add('is-selected');
        }
    }

    function refreshList() {
        visible = filterBuildings(buildings, search.value);
        const ids = new Set(visible.map((building) => building.id));
        for (const row of listRows) row.hidden = !ids.has(row.dataset.mapBuilding);
        setStatus(root.querySelector('[data-map-results]'), `${visible.length} ${visible.length === 1 ? 'building' : 'buildings'}`);
        root.querySelector('[data-map-no-results]').hidden = visible.length > 0 || buildings.length === 0;
        const located = visible.filter(hasBuildingLocation);
        fitButton.disabled = located.length === 0;
        root.querySelector('[data-map-empty]').hidden = located.length > 0;
        refreshMarkers();
    }

    function fitBuildings() {
        const located = visible.filter(hasBuildingLocation);
        if (!map || !located.length) return;
        map.fitBounds(L.latLngBounds(located.map((building) => [building.latitude, building.longitude])), { padding: [45, 45], maxZoom: 17 });
    }

    function updateMapLayout() {
        if (disposed || !map || !(canvas.clientWidth > 0 && canvas.clientHeight > 0)) return;
        map.invalidateSize({ pan: false });
        if (initialFitPending) {
            initialFitPending = false;
            fitBuildings();
        }
    }

    function updateTileStatus() {
        if (disposed) return;
        if (!basemapToggle.checked) {
            setStatus(status, 'Street map off. Building locations remain available.');
            retryButton.hidden = true;
        } else if (!tiles) {
            setStatus(status, 'Street map is not configured. Building locations remain available.');
            retryButton.hidden = true;
        } else if (tileFailures) {
            setStatus(status, tileSuccesses ? 'Some street tiles are unavailable. Building locations remain available.'
                : 'Street map unavailable. Building locations remain available on the neutral background.');
            retryButton.hidden = false;
        } else {
            setStatus(status, tileSuccesses ? 'Select a marker or a building from the list.' : 'Loading street map…');
            retryButton.hidden = true;
        }
    }

    function toggleBasemap() {
        if (!map || !tiles) return;
        if (basemapToggle.checked) tiles.addTo(map);
        else map.removeLayer(tiles);
        updateTileStatus();
    }

    function retryTiles() {
        if (!tiles || disposed) return;
        tileFailures = 0;
        tileSuccesses = 0;
        tiles.redraw();
        updateTileStatus();
    }

    function onRootClick(event) {
        const button = event.target.closest?.('[data-map-select]');
        if (button && root.contains(button)) selectBuilding(button.dataset.mapSelect);
    }

    root.addEventListener('click', onRootClick);
    search.addEventListener('input', refreshList);
    fitButton.addEventListener('click', fitBuildings);
    retryButton.addEventListener('click', retryTiles);
    basemapToggle.addEventListener('change', toggleBasemap);
    refreshList();

    const controller = {
        selectBuilding,
        dispose() {
            if (disposed) return;
            disposed = true;
            root.removeEventListener('click', onRootClick);
            search.removeEventListener('input', refreshList);
            fitButton.removeEventListener('click', fitBuildings);
            retryButton.removeEventListener('click', retryTiles);
            basemapToggle.removeEventListener('change', toggleBasemap);
            resizeObserver?.disconnect();
            if (layoutFrame !== null) windowRoot.cancelAnimationFrame?.(layoutFrame);
            tiles?.off();
            map?.remove();
            markersById.clear();
            contextHost?.replaceChildren();
            contextTargetId = null;
            controllers.delete(root);
            delete root.dataset.mapInitialized;
        },
    };
    controllers.set(root, controller);
    root.dataset.mapInitialized = 'loading';
    const load = dependencies.loadLeaflet ?? (() => {
        leafletImport ??= import('../vendor/leaflet/leaflet-src.esm.js').catch((error) => { leafletImport = null; throw error; });
        return leafletImport;
    });
    controller.ready = Promise.resolve().then(load).then((module) => {
        if (disposed) return;
        L = module;
        map = L.map(canvas, { center: [20, 0], zoom: 2, maxZoom: 19, scrollWheelZoom: false, worldCopyJump: true });
        markerLayer = L.layerGroup().addTo(map);
        L.control.scale({ imperial: false }).addTo(map);
        const url = root.dataset.tileUrl || '';
        try {
            const test = new URL(url.replace(/\{[^{}]+\}/g, '0'), windowRoot.location.origin);
            if (url && ['http:', 'https:'].includes(test.protocol)) {
                tiles = L.tileLayer(url, {
                    maxZoom: 19,
                    // OSM requires a browser referrer; send only this application's origin across sites.
                    referrerPolicy: 'strict-origin-when-cross-origin',
                    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
                });
                tiles.on('tileerror', () => { tileFailures += 1; updateTileStatus(); });
                tiles.on('tileload', () => { tileSuccesses += 1; updateTileStatus(); });
                if (basemapToggle.checked) tiles.addTo(map);
            }
        } catch { /* A malformed tile template leaves the local building layer available. */ }
        refreshMarkers();
        const Observer = dependencies.ResizeObserver ?? windowRoot.ResizeObserver;
        if (Observer) {
            resizeObserver = new Observer(updateMapLayout);
            resizeObserver.observe(canvas);
        }
        // HTMX can insert the fragment before its map CSS establishes a canvas size.
        // Fit after layout, then keep later resizes from resetting the user's zoom.
        if (windowRoot.requestAnimationFrame) {
            layoutFrame = windowRoot.requestAnimationFrame(() => {
                layoutFrame = null;
                updateMapLayout();
            });
        } else updateMapLayout();
        updateTileStatus();
        root.dataset.mapInitialized = 'true';
        if (selected) selectBuilding(selected);
    }).catch(() => {
        if (disposed) return;
        setStatus(status, 'The interactive map could not be loaded. You can still open buildings from the list.');
        root.dataset.mapInitialized = 'error';
        fitButton.disabled = true;
        basemapToggle.disabled = true;
        retryButton.hidden = true;
    });
    return controller;
}

export function mountBuildingMaps(scope = document) {
    if (scope.matches?.('[data-building-map]')) initializeBuildingMap(scope);
    for (const root of scope.querySelectorAll?.('[data-building-map]') ?? []) initializeBuildingMap(root);
}

export function disposeBuildingMapsWithin(element) {
    for (const [root, controller] of controllers) {
        if (element === root || element?.contains?.(root)) controller.dispose();
    }
}

if (typeof document !== 'undefined') {
    mountBuildingMaps();
    document.body.addEventListener('htmx:after:settle', () => mountBuildingMaps());
    document.body.addEventListener('htmx:before:cleanup', (event) => disposeBuildingMapsWithin(event.target));
}
