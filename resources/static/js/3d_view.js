import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { prepareModelSurfaces } from './viewer_surfaces.js?v=0.5.0-3';
import { renderMaterialInspector } from './viewer_inspector.js?v=0.5.0-3';
import { fitShadowToBounds, recenterModel, updateCameraClipping, fitCameraToBounds, createRenderResources, disposeModelResources, createOutdoorLights } from './viewer_rendering.js?v=0.8.0';
import { bindPageResourceLifecycle } from './worker_page_lifecycle.js';
import { createGeometryActivity, loadGeometry } from './geometry_loading.js?v=0.8.0';
import { createLandscape } from './viewer_landscape.js?v=0.8.0';

const IDENTITY_KEYS = new Set([
    'name', 'type', 'ifctype', 'globalid', 'global_id', 'guid', 'id',
]);

export function formatMetricValue(value) {
    if (typeof value === 'number') {
        if (!Number.isFinite(value)) return '—';
        return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
    }
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    if (typeof value === 'string' && value.trim()) return value.trim();
    return '—';
}

function getMetadataValue(userData, keys) {
    for (const key of keys) {
        if (userData[key] !== undefined) return userData[key];
    }
    const match = Object.entries(userData).find(([key]) =>
        keys.includes(key.toLowerCase()),
    );
    return match?.[1];
}

function metricLabel(key) {
    return key
        .replace(/[_-]+/g, ' ')
        .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
        .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function extractPartMetrics(part) {
    const userData = part?.userData ?? {};
    const rawName = getMetadataValue(userData, ['Name', 'name']) || part?.name || '';
    const name = String(rawName).trim() && !/^product-[0-9a-f-]{36}$/i.test(String(rawName)) ? String(rawName).trim() : (getMetadataValue(userData, ['ifctype', 'type']) || 'Unnamed building part');
    const type = getMetadataValue(userData, ['ifctype', 'type']) || 'Mesh';
    const guid = getMetadataValue(userData, ['globalid', 'global_id', 'guid']) || 'Not available';
    const metrics = [];

    for (const [key, value] of Object.entries(userData)) {
        if (IDENTITY_KEYS.has(key.toLowerCase())) continue;
        if (!['string', 'number', 'boolean'].includes(typeof value)) continue;
        const formatted = formatMetricValue(value);
        if (formatted !== '—') metrics.push({ label: metricLabel(key), value: formatted });
    }

    const geometry = part?.geometry;
    if (geometry) {
        if (!geometry.boundingBox && typeof geometry.computeBoundingBox === 'function') {
            geometry.computeBoundingBox();
        }
        if (geometry.boundingBox) {
            const { min, max } = geometry.boundingBox;
            const dimensions = [max.x - min.x, max.y - min.y, max.z - min.z]
                .map(formatMetricValue)
                .join(' × ');
            metrics.push({ label: 'Dimensions (m)', value: dimensions });
        }
        const vertexCount = geometry.index?.count ?? geometry.attributes?.position?.count;
        if (Number.isFinite(vertexCount)) {
            metrics.push({ label: 'Triangles', value: formatMetricValue(vertexCount / 3) });
        }
    }

    return {
        name: String(name),
        type: String(type),
        guid: String(guid),
        metrics,
    };
}

function metadataOwner(object, model) {
    let current=object, fallback=object;
    while(current && current!==model){
        if(getMetadataValue(current.userData??{},['globalid','global_id','guid']))return current;
        if(current.name || Object.keys(current.userData??{}).length)fallback=current;
        current=current.parent;
    }
    return fallback;
}

export function findIfcMesh(model, requested) {
    let match=null;
    model.traverse(child=>{
        if(!match && child.isMesh){
            const owner=metadataOwner(child,model);
            const guid=getMetadataValue(owner.userData??{},['globalid','global_id','guid']);
            if(guid!=null && String(guid)===requested)match=child;
        }
    });
    return match;
}

// A native select exposes the same IFC identities as pointer picking without
// requiring sight or fine pointer movement. A product with several surfaces
// appears once; hidden spaces and hidden ancestor groups stay out of the list.
export function collectSelectableParts(model) {
    const choices = [], identities = new Set();
    model.traverse(mesh => {
        if (!mesh.isMesh) return;
        for (let current = mesh; current; current = current.parent) {
            if (current.visible === false) return;
            if (current === model) break;
        }
        const owner = metadataOwner(mesh, model);
        const details = extractPartMetrics({name: owner.name, userData: owner.userData});
        const guid = details.guid === 'Not available' ? '' : details.guid;
        const identity = guid || owner;
        if (identities.has(identity)) return;
        identities.add(identity);
        choices.push({mesh, owner, guid, label: `${details.name} · ${details.type}${guid ? ` · ${guid}` : ''}`});
    });
    return choices;
}

export function bindViewerKeyboard(canvas, controls, {fitModel, clearSelection}) {
    controls.listenToKeyEvents(canvas);
    const onKey = event => {
        if (event.ctrlKey || event.metaKey || event.altKey) return;
        const key = event.key.toLowerCase();
        if (key === 'escape') { event.preventDefault(); clearSelection(); }
        else if (key === 'f') { event.preventDefault(); fitModel(); }
        else if (['+', '=', '-', '_'].includes(key)) {
            event.preventDefault();
            const offset = controls.object.position.clone().sub(controls.target), distance = offset.length();
            if (!distance) return;
            const nextDistance = THREE.MathUtils.clamp(distance * (key === '+' || key === '=' ? 0.8 : 1.25),
                controls.minDistance, controls.maxDistance);
            controls.object.position.copy(controls.target).add(offset.multiplyScalar(nextDistance / distance));
            controls.update();
        }
    };
    canvas.addEventListener('keydown', onKey);
    return () => { canvas.removeEventListener('keydown', onKey); controls.stopListenToKeyEvents(); };
}

export function mountViewerHeader(root, documentRoot = globalThis.document) {
    const toolbar = root?.querySelector?.('[data-viewer-header-content]');
    const slot = documentRoot?.getElementById?.('viewer-header-slot');
    if (!toolbar || !slot) return () => {};
    const originalParent = toolbar.parentNode, originalNext = toolbar.nextSibling;
    function movePreservingFocus(move) {
        const active = documentRoot.activeElement;
        const movingFocus = active && toolbar.contains?.(active);
        move();
        // Reparenting a focused heading/control can reset focus to body. Only
        // restore that moved node, never override another control's focus.
        if (movingFocus && active.isConnected
            && (!documentRoot.activeElement || documentRoot.activeElement === documentRoot.body)) {
            active.focus({preventScroll: true});
        }
    }

    documentRoot.body?.classList?.add('viewer-active');
    movePreservingFocus(() => slot.replaceChildren(toolbar));

    return () => {
        if (slot.contains(toolbar)) {
            // BFCache keeps this page attached. Restore its controls so a
            // fresh renderer can mount them after persisted pageshow.
            if (root.isConnected && originalParent) movePreservingFocus(() => originalParent.insertBefore(toolbar, originalNext?.parentNode === originalParent ? originalNext : null));
            else toolbar.remove();
        }
        documentRoot.body?.classList?.remove('viewer-active');
    };
}

export function initializeViewer(root) {
    if (!root || root.dataset.viewerInitialized === 'true') return null;
    root.dataset.viewerInitialized = 'true';
    document.title = `${root.querySelector('#viewer-title')?.textContent || '3D model viewer'} · Cadevil`;
    const element = id => root.querySelector(`#${id}`);
    const canvas = element('viewer-canvas'), viewport = element('viewer-viewport');
    const loadingEl = element('loading-overlay'), loadingText = element('loading-text'), loadingBar = element('loading-bar');
    const loading = createGeometryActivity({ target: viewport, overlay: loadingEl, message: loadingText, detail: element('loading-detail'), progress: loadingBar });
    const errorOverlay = element('error-overlay'), errorMessage = element('error-message');
    const selectionStatus = element('selection-status'), selectionEmpty = element('selection-empty'), selectionDetails = element('selection-details');
    const metricsEl = element('part-metrics'), metricsEmpty = element('metrics-empty');
    const materialEl = element('part-materials'), metadataStatus = element('material-data-status');
    const clearButton = root.querySelector('[data-viewer-action="clear-selection"]');
    const partSelect = element('viewer-part-select');
    const fitButton = root.querySelector('[data-viewer-action="fit"]');
    const gridButton = root.querySelector('[data-viewer-action="toggle-grid"]');
    const textureToggle = element('viewer-textures'), spaceToggle = element('viewer-spaces'), landscapeToggle = element('viewer-landscape');
    const cleanupViewerHeader = mountViewerHeader(root);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(getComputedStyle(viewport).backgroundColor);
    let renderer, environment;
    try { ({ renderer, environment } = createRenderResources(canvas, scene)); }
    catch (error) {
        cleanupViewerHeader();
        root.dataset.viewerInitialized = 'false';
        loading.finish();
        if (partSelect) partSelect.options[0].textContent = 'Building parts unavailable';
        errorMessage.textContent = '3D rendering could not start. Check that WebGL 2 and hardware acceleration are available, then reload this page.';
        errorOverlay.classList.add('is-visible');
        console.error('Viewer initialization failed:', error);
        return null;
    }
    const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 2000);
    camera.position.set(30, 30, 30);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = !matchMedia('(prefers-reduced-motion: reduce)').matches;
    controls.dampingFactor = 0.06;
    controls.screenSpacePanning = true;
    const outdoorLights = createOutdoorLights(scene), sun = outdoorLights.sun;
    const grid = new THREE.GridHelper(100, 40, 0x999999, 0xcccccc);
    grid.material.opacity = 0.3;
    grid.material.transparent = true;
    grid.material.depthWrite = false;
    scene.add(grid);
    let model = null, modelBounds = null, sceneBounds = null, selectionOutline = null, selectedMesh = null, surfaces = null, landscape = null;
    let disposed = false, renderFailed = false, metadata = null, metadataError = '';
    let renderFrame = null, renderCount = 0, geometryReady = false;
    const spaces = [], listeners = [], abortMetadata = new AbortController(), abortGeometry = new AbortController();
    const detailsCache = new Map();
    let abortDetails = null, selectionVersion = 0, detailsTask = null;
    const raycaster = new THREE.Raycaster(), pointer = new THREE.Vector2();
    let pointerStart = null;
    let partChoices = [];
    function listen(target, event, callback) { target.addEventListener(event, callback); listeners.push([target, event, callback]); }
    function requestRender() {
        if (disposed || renderFailed || renderFrame !== null) return;
        renderFrame = requestAnimationFrame(() => {
            renderFrame = null;
            if (disposed) return;
            // OrbitControls emits change while damping settles, requesting only
            // the frames that have a visible camera change.
            try {
                controls.update(); updateCameraClipping(camera, controls, sceneBounds || modelBounds);
                scene.background.set(getComputedStyle(viewport).backgroundColor);
                renderer.render(scene, camera);
                root.dataset.renderFrames = String(++renderCount);
                if (geometryReady) { loading.finish(); geometryReady = false; root.dataset.viewerReady = 'true'; }
            } catch (error) {
                renderFailed = true; loading.finish();
                errorMessage.textContent = 'The 3D view could not be rendered. Reload this page to try again.';
                errorOverlay.classList.add('is-visible'); console.error('Viewer rendering failed:', error);
            }
        });
    }
    function resize() {
        const width = Math.max(viewport.clientWidth, 1), height = Math.max(viewport.clientHeight, 1);
        camera.aspect = width / height;
        renderer.setSize(width, height, false);
        camera.updateProjectionMatrix();
        requestRender();
    }
    function fitBounds(bounds) {
        if (!bounds || bounds.isEmpty()) return;
        const center = fitCameraToBounds(camera, bounds);
        controls.target.copy(center);
        controls.update();
        updateCameraClipping(camera, controls, modelBounds || bounds);
    }
    function fitModel() { fitBounds(modelBounds); }
    function clearSelection() {
        if (selectionOutline) {
            scene.remove(selectionOutline);
            selectionOutline.geometry.dispose(); selectionOutline.material.dispose(); selectionOutline = null;
        }
        selectedMesh = null; selectionVersion++; abortDetails?.abort(); abortDetails = null;
        if (detailsTask != null) globalThis.CadevilActivity?.finish(detailsTask);
        detailsTask = null; materialEl.setAttribute('aria-busy', 'false');
        selectionStatus.textContent = 'Nothing selected';
        if (partSelect) partSelect.value = '';
        selectionEmpty.hidden = false; selectionDetails.hidden = true; clearButton.disabled = true;
        requestRender();
    }
    function renderSelectedProperties() {
        if (!selectedMesh) return;
        const owner = metadataOwner(selectedMesh, model);
        const guid = String(getMetadataValue(owner.userData ?? {}, ['globalid', 'global_id', 'guid']) || '');
        const record = detailsCache.get(guid);
        if (record) { materialEl.setAttribute('aria-busy', 'false'); renderMaterialInspector(materialEl, record); return; }
        renderMaterialInspector(materialEl, null, { loading: !metadataError, error: metadataError });
        if (abortDetails) return;
        const version = selectionVersion;
        abortDetails = new AbortController();
        materialEl.setAttribute('aria-busy', 'true');
        detailsTask = globalThis.CadevilActivity?.start({ label: 'Loading selected material properties', target: materialEl });
        const url = new URL(root.dataset.metadataUrl, location.href);
        url.searchParams.set('element', guid);
        fetch(url, { signal: abortDetails.signal, credentials: 'same-origin', headers: { Accept: 'application/json' } })
            .then(async response => { if (!response.ok) throw new Error('Properties for this IFC element could not be read. You can continue exploring the model.'); return response.json(); })
            .then(data => {
                if (disposed || version !== selectionVersion) return;
                abortDetails = null;
                if (detailsTask != null) globalThis.CadevilActivity?.finish(detailsTask);
                detailsTask = null; materialEl.setAttribute('aria-busy', 'false');
                const record = data.elements?.[guid];
                if (record) {
                    if (detailsCache.size >= 32) detailsCache.delete(detailsCache.keys().next().value);
                    detailsCache.set(guid, record);
                }
                renderMaterialInspector(materialEl, record);
                if (data.assessment?.notice) metadataStatus.textContent = (data.assessment?.status === 'selected' && data.assessment?.complete === false ? 'Incomplete saved assessment. ' : '') + data.assessment.notice;
            }).catch(error => {
                if (disposed || version !== selectionVersion || error.name === 'AbortError') return;
                abortDetails = null;
                if (detailsTask != null) globalThis.CadevilActivity?.finish(detailsTask);
                detailsTask = null; materialEl.setAttribute('aria-busy', 'false');
                renderMaterialInspector(materialEl, null, { error: error.message });
            });
    }
    function showSelection(mesh) {
        clearSelection(); selectedMesh = mesh;
        const source = metadataOwner(mesh, model);
        const details = extractPartMetrics(source === mesh ? mesh : { name: source.name, userData: source.userData, geometry: mesh.geometry });
        if (partSelect) partSelect.value = String(partChoices.findIndex(choice => choice.owner === source || (choice.guid && choice.guid === details.guid)));
        element('part-name').textContent = details.name;
        element('part-type').textContent = details.type;
        element('part-guid').textContent = details.guid;
        metricsEl.replaceChildren();
        for (const metric of details.metrics) {
            const row = document.createElement('div'), label = document.createElement('dt'), value = document.createElement('dd');
            label.textContent = metric.label; value.textContent = metric.value; row.append(label, value); metricsEl.append(row);
        }
        metricsEmpty.hidden = details.metrics.length > 0;
        selectionStatus.textContent = `${details.name} selected`;
        selectionEmpty.hidden = true; selectionDetails.hidden = false; clearButton.disabled = false;
        renderSelectedProperties();
        selectionOutline = new THREE.BoxHelper(source, 0x888888);
        selectionOutline.material.depthTest = false; selectionOutline.material.transparent = true;
        selectionOutline.material.opacity = 0.85; selectionOutline.renderOrder = 999;
        scene.add(selectionOutline);
        requestRender();
    }
    function selectAt(clientX, clientY) {
        if (!model) return;
        const rect = canvas.getBoundingClientRect();
        pointer.x = ((clientX - rect.left) / rect.width) * 2 - 1;
        pointer.y = -((clientY - rect.top) / rect.height) * 2 + 1;
        raycaster.setFromCamera(pointer, camera);
        const hit = raycaster.intersectObject(model, true).find(({ object }) => object.isMesh && object.visible);
        if (hit) showSelection(hit.object); else clearSelection();
    }
    function refreshPartChoices() {
        if (!partSelect || !model) return;
        partChoices = collectSelectableParts(model);
        const placeholder = document.createElement('option');
        placeholder.value = ''; placeholder.textContent = partChoices.length ? 'Choose a building part' : 'No visible building parts';
        const options = partChoices.map((choice, index) => {
            const option = document.createElement('option');
            option.value = String(index); option.textContent = choice.label;
            return option;
        });
        partSelect.replaceChildren(placeholder, ...options);
        partSelect.disabled = partChoices.length === 0;
        const owner = selectedMesh && metadataOwner(selectedMesh, model);
        const index = partChoices.findIndex(choice => choice.owner === owner);
        partSelect.value = index >= 0 ? String(index) : '';
    }
    function configureShadows() {
        model?.traverse(child => {
            if (!child.isMesh) return;
            const materialList = Array.isArray(child.material) ? child.material : [child.material];
            const isSpace = String(getMetadataValue(metadataOwner(child, model).userData ?? {}, ['ifctype', 'type'])).toLowerCase().match(/^(ifcspace|ifcopeningelement)$/);
            child.castShadow = !isSpace && materialList.every(material => material && !material.transparent && !material.transmission);
            child.receiveShadow = !isSpace && materialList.some(material => material && !material.transparent);
        });
        renderer.shadowMap.needsUpdate = true;
    }
    function applySurfaces() {
        surfaces?.dispose(); surfaces = null;
        if (model && metadata) {
            surfaces = prepareModelSurfaces(model, metadata.elements, renderer, { textures: textureToggle.checked });
            const count = surfaces.counts.texturedMeshes;
            element('surface-status').textContent = textureToggle.checked ? `${formatMetricValue(count)} surfaces textured from declared material names. Original IFC colors and authored textures are retained.` : 'Original IFC appearance';
        } else element('surface-status').textContent = textureToggle.checked ? metadataError ? 'Original IFC appearance · material data unavailable' : 'Waiting for IFC material data…' : 'Original IFC appearance';
        configureShadows();
        requestRender();
    }
    listen(canvas, 'pointerdown', event => { pointerStart = { x: event.clientX, y: event.clientY }; });
    listen(canvas, 'pointercancel', () => { pointerStart = null; });
    listen(canvas, 'pointerup', event => {
        if (!pointerStart) return;
        const travel = Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y); pointerStart = null;
        if (travel < 5) selectAt(event.clientX, event.clientY);
    });
    const cleanupKeyboard = bindViewerKeyboard(canvas, controls, {fitModel, clearSelection});
    if (partSelect) listen(partSelect, 'change', () => {
        const choice = partSelect.value === '' ? null : partChoices[Number(partSelect.value)];
        if (choice) { showSelection(choice.mesh); fitBounds(new THREE.Box3().setFromObject(choice.owner)); }
        else clearSelection();
    });
    listen(fitButton, 'click', fitModel); listen(clearButton, 'click', clearSelection);
    listen(gridButton, 'click', () => { grid.visible = !grid.visible; gridButton.setAttribute('aria-pressed', String(grid.visible)); requestRender(); });
    listen(textureToggle, 'change', applySurfaces);
    if (landscapeToggle) listen(landscapeToggle, 'change', () => { if (landscape) landscape.group.visible = landscapeToggle.checked; requestRender(); });
    listen(spaceToggle, 'change', () => {
        for (const space of spaces) space.visible = spaceToggle.checked;
        if (selectedMesh && !selectedMesh.visible) clearSelection();
        refreshPartChoices();
        requestRender();
    });
    listen(controls, 'change', requestRender);
    const themeObserver = new MutationObserver(requestRender);
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style', 'data-theme'] });
    themeObserver.observe(document.body, { attributes: true, attributeFilter: ['class', 'style', 'data-theme'] });
    listen(matchMedia('(prefers-color-scheme: dark)'), 'change', requestRender);
    const resizeObserver = new ResizeObserver(resize); resizeObserver.observe(viewport); resize();
    // Fetch independently of tessellation: unreadable properties never block geometry.
    const materialTask = globalThis.CadevilActivity?.start({ label: 'Loading IFC material data', target: textureToggle.closest('details') });
    const materialOptions = textureToggle.closest('details');
    materialOptions?.setAttribute('aria-busy', 'true');
    metadataStatus.textContent = `Loading IFC material data… ${metadataStatus.textContent.trim()}`;
    fetch(root.dataset.metadataUrl, { signal: abortMetadata.signal, credentials: 'same-origin', headers: { Accept: 'application/json' } })
        .then(async response => { if (!response.ok) throw new Error('IFC material properties could not be loaded. The 3D model remains available.'); return response.json(); })
        .then(data => {
            if (disposed) return;
            metadata = data;
            metadataStatus.textContent = (data.assessment?.status === 'selected' && data.assessment?.complete === false ? 'Incomplete saved assessment. ' : '') + (data.assessment?.notice || 'Declared IFC properties');
            if (data.source?.warnings?.length) metadataStatus.textContent += ` ${data.source.warnings.length} IFC property records are partially unreadable.`;
            applySurfaces(); renderSelectedProperties();
        }).catch(error => {
            if (disposed || error.name === 'AbortError') return;
            metadataError = error.message;
            metadataStatus.textContent = metadataError;
            element('surface-status').textContent = 'Original IFC appearance · material data unavailable';
            renderSelectedProperties();
        }).finally(() => {
            if (materialTask != null) globalThis.CadevilActivity?.finish(materialTask);
            materialOptions?.setAttribute('aria-busy', 'false');
        });
    loadGeometry(new URL(root.dataset.modelUrl, location.href).href, {
        loader: new GLTFLoader(), signal: abortGeometry.signal,
        onStage: loading.stage, onProgress: loading.transfer,
    }).then(gltf => {
        if (disposed) { disposeModelResources(gltf.scene); return; }
        loading.stage('rendering');
        model = gltf.scene; let partCount = 0;
        model.traverse(child => {
            if (!child.isMesh) return;
            partCount++;
            if (String(getMetadataValue(metadataOwner(child, model).userData ?? {}, ['ifctype', 'type'])).toLowerCase().match(/^(ifcspace|ifcopeningelement)$/)) { spaces.push(child); child.visible = false; }
        });
        refreshPartChoices();
        modelBounds = recenterModel(model); scene.add(model);
        landscape = createLandscape(modelBounds); landscape.group.visible = landscapeToggle?.checked ?? true; scene.add(landscape.group);
        sceneBounds = modelBounds.clone().union(new THREE.Box3().setFromObject(landscape.group));
        const size = modelBounds.getSize(new THREE.Vector3()), span = Math.max(size.x, size.y, size.z, 1);
        controls.maxDistance = span * 8; controls.minDistance = span * 0.001;
        grid.scale.setScalar(Math.max(size.x, size.z, 1) * 1.35 / 100);
        grid.position.y = modelBounds.min.y - Math.max(span * 0.002, 0.01);
        fitShadowToBounds(sun, modelBounds);
        element('model-part-count').textContent = formatMetricValue(partCount);
        element('model-size').textContent = [size.x, size.y, size.z].map(formatMetricValue).join(' × ') + ' m';
        spaceToggle.disabled = spaces.length === 0;
        element('space-count').textContent = `${formatMetricValue(spaces.length)} space and opening surfaces · hidden initially`;
        applySurfaces(); fitModel(); geometryReady = true; requestRender();
        const requested = root.dataset.selectedElement;
        if (requested) {
            const match = findIfcMesh(model, requested);
            if (match) {
                if (spaces.includes(match)) { spaceToggle.checked = true; for (const space of spaces) space.visible = true; }
                refreshPartChoices();
                showSelection(match); fitBounds(new THREE.Box3().setFromObject(metadataOwner(match, model)));
            } else selectionStatus.textContent = 'The requested IFC element has no renderable geometry in this model.';
        }
    }).catch(error => {
        loading.finish();
        if (disposed || error.name === 'AbortError') return;
        console.error('GLB load error:', error);
        if (partSelect) partSelect.options[0].textContent = 'Building parts unavailable';
        errorMessage.textContent = error?.message || 'The model could not be loaded.'; errorOverlay.classList.add('is-visible');
    });
    function dispose() {
        if (disposed) return;
        disposed = true; abortMetadata.abort(); abortGeometry.abort(); abortDetails?.abort(); loading.finish();
        if (materialTask != null) globalThis.CadevilActivity?.finish(materialTask);
        materialOptions?.setAttribute('aria-busy', 'false');
        cancelAnimationFrame(renderFrame); renderFrame = null;
        resizeObserver.disconnect(); themeObserver.disconnect(); clearSelection(); surfaces?.dispose(); landscape?.dispose(); disposeModelResources(model);
        grid.geometry.dispose(); grid.material.dispose(); environment.dispose();
        outdoorLights.dispose(); cleanupKeyboard(); controls.dispose(); renderer.dispose(); cleanupViewerHeader();
        for (const [target, event, callback] of listeners) target.removeEventListener(event, callback);
        releaseLifecycle();
        delete root.dataset.viewerInitialized;
    }
    const releaseLifecycle = bindPageResourceLifecycle(root, dispose);
    return { clearSelection, dispose, fitModel };
}

if (typeof document !== 'undefined') {
    initializeViewer(document.getElementById('viewer-app'));
    document.addEventListener('htmx:after:settle', () => initializeViewer(document.getElementById('viewer-app')));
    window.addEventListener('pageshow', event => { if (event.persisted) initializeViewer(document.getElementById('viewer-app')); });
}
