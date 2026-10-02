import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

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

export function mountViewerHeader(root, documentRoot = globalThis.document) {
    const toolbar = root?.querySelector?.('[data-viewer-header-content]');
    const slot = documentRoot?.getElementById?.('viewer-header-slot');
    if (!toolbar || !slot) return () => {};

    slot.replaceChildren(toolbar);
    documentRoot.body?.classList?.add('viewer-active');

    return () => {
        if (slot.contains(toolbar)) toolbar.remove();
        documentRoot.body?.classList?.remove('viewer-active');
    };
}

export function initializeViewer(root) {
    if (!root || root.dataset.viewerInitialized === 'true') return null;
    root.dataset.viewerInitialized = 'true';

    const element = (id) => root.querySelector(`#${id}`);
    const canvas = element('viewer-canvas');
    const viewport = element('viewer-viewport');
    const loadingEl = element('loading-overlay');
    const loadingText = element('loading-text');
    const loadingBar = element('loading-bar');
    const errorOverlay = element('error-overlay');
    const errorMessage = element('error-message');
    const selectionStatus = element('selection-status');
    const selectionEmpty = element('selection-empty');
    const selectionDetails = element('selection-details');
    const metricsEl = element('part-metrics');
    const metricsEmpty = element('metrics-empty');
    const clearButton = root.querySelector('[data-viewer-action="clear-selection"]');
    const fitButton = root.querySelector('[data-viewer-action="fit"]');
    const gridButton = root.querySelector('[data-viewer-action="toggle-grid"]');
    const cleanupViewerHeader = mountViewerHeader(root);

    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x20242a);
    scene.fog = new THREE.FogExp2(0x20242a, 0.0015);

    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 2000);
    camera.position.set(30, 30, 30);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.06;
    controls.screenSpacePanning = false;

    scene.add(new THREE.HemisphereLight(0xf5f7ff, 0x353b43, 1.6));
    const sun = new THREE.DirectionalLight(0xfff4e0, 2.3);
    sun.position.set(50, 80, 50);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.camera.near = 0.5;
    sun.shadow.camera.far = 500;
    scene.add(sun);
    const fill = new THREE.DirectionalLight(0x9bb7ff, 0.55);
    fill.position.set(-50, 20, -50);
    scene.add(fill);

    const grid = new THREE.GridHelper(200, 40, 0x999999, 0xcccccc);
    grid.material.opacity = 0.36;
    grid.material.transparent = true;
    scene.add(grid);

    let model = null;
    let modelBounds = null;
    let selectionOutline = null;
    let disposed = false;
    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    let pointerStart = null;

    function resize() {
        const width = Math.max(viewport.clientWidth, 1);
        const height = Math.max(viewport.clientHeight, 1);
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
        renderer.setSize(width, height, false);
    }

    function fitModel() {
        if (!modelBounds || modelBounds.isEmpty()) return;
        const center = modelBounds.getCenter(new THREE.Vector3());
        const size = modelBounds.getSize(new THREE.Vector3());
        const maxDim = Math.max(size.x, size.y, size.z, 1);
        const distance = (maxDim / (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)))) * 1.45;
        camera.position.set(
            center.x + distance * 0.7,
            center.y + distance * 0.55,
            center.z + distance * 0.7,
        );
        camera.near = Math.max(distance / 100, 0.01);
        camera.far = Math.max(distance * 12, 100);
        camera.updateProjectionMatrix();
        controls.target.copy(center);
        controls.update();
    }

    function clearSelection() {
        if (selectionOutline) {
            scene.remove(selectionOutline);
            selectionOutline.geometry.dispose();
            selectionOutline.material.dispose();
            selectionOutline = null;
        }
        selectionStatus.textContent = 'Nothing selected';
        selectionEmpty.hidden = false;
        selectionDetails.hidden = true;
        clearButton.disabled = true;
    }

    function showSelection(mesh) {
        clearSelection();
        const source = metadataOwner(mesh, model);
        const details = extractPartMetrics(source === mesh ? mesh : {
            name: source.name,
            userData: source.userData,
            geometry: mesh.geometry,
        });
        element('part-name').textContent = details.name;
        element('part-type').textContent = details.type;
        element('part-guid').textContent = details.guid;
        metricsEl.replaceChildren();
        for (const metric of details.metrics) {
            const row = document.createElement('div');
            const label = document.createElement('dt');
            const value = document.createElement('dd');
            label.textContent = metric.label;
            value.textContent = metric.value;
            row.append(label, value);
            metricsEl.append(row);
        }
        metricsEmpty.hidden = details.metrics.length > 0;
        selectionStatus.textContent = `${details.name} selected`;
        selectionEmpty.hidden = true;
        selectionDetails.hidden = false;
        clearButton.disabled = false;
        selectionOutline = new THREE.BoxHelper(mesh, 0x69a7ff);
        selectionOutline.material.depthTest = false;
        selectionOutline.renderOrder = 999;
        scene.add(selectionOutline);
    }

    function selectAt(clientX, clientY) {
        if (!model) return;
        const rect = canvas.getBoundingClientRect();
        pointer.x = ((clientX - rect.left) / rect.width) * 2 - 1;
        pointer.y = -((clientY - rect.top) / rect.height) * 2 + 1;
        raycaster.setFromCamera(pointer, camera);
        const hit = raycaster.intersectObject(model, true).find(({ object }) => object.isMesh);
        if (hit) showSelection(hit.object);
        else clearSelection();
    }

    canvas.addEventListener('pointerdown', (event) => {
        pointerStart = { x: event.clientX, y: event.clientY };
    });
    canvas.addEventListener('pointerup', (event) => {
        if (!pointerStart) return;
        const travel = Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y);
        pointerStart = null;
        if (travel < 5) selectAt(event.clientX, event.clientY);
    });
    canvas.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') clearSelection();
        if (event.key.toLowerCase() === 'f') fitModel();
    });

    fitButton.addEventListener('click', fitModel);
    clearButton.addEventListener('click', clearSelection);
    gridButton.addEventListener('click', () => {
        grid.visible = !grid.visible;
        gridButton.setAttribute('aria-pressed', String(grid.visible));
    });

    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(viewport);
    resize();

    new GLTFLoader().load(
        root.dataset.modelUrl,
        (gltf) => {
            model = gltf.scene;
            let partCount = 0;
            model.traverse((child) => {
                if (child.isMesh) {
                    partCount += 1;
                    child.castShadow = true;
                    child.receiveShadow = true;
                }
            });
            scene.add(model);
            modelBounds = new THREE.Box3().setFromObject(model);
            const size = modelBounds.getSize(new THREE.Vector3());
            grid.position.y = modelBounds.min.y;
            element('model-part-count').textContent = formatMetricValue(partCount);
            element('model-size').textContent = [size.x, size.y, size.z]
                .map(formatMetricValue)
                .join(' × ');
            fitModel();
            loadingEl.hidden = true;
            const requested=root.dataset.selectedElement;
            if(requested){
                const match=findIfcMesh(model,requested);
                if(match){
                    showSelection(match);
                    const whole=modelBounds;modelBounds=new THREE.Box3().setFromObject(metadataOwner(match,model));fitModel();modelBounds=whole;
                }else{selectionStatus.textContent='The requested IFC element has no renderable geometry in this model.';}
            }
        },
        (xhr) => {
            if (xhr.lengthComputable) {
                const percent = Math.round((xhr.loaded / xhr.total) * 100);
                loadingBar.value = percent;
                loadingText.textContent = `Loading 3D model… ${percent}%`;
            } else {
                const kilobytes = Math.round(xhr.loaded / 1024);
                loadingText.textContent = `Loading 3D model… ${kilobytes} KB received`;
            }
        },
        (error) => {
            console.error('GLB load error:', error);
            loadingEl.hidden = true;
            errorMessage.textContent = error?.message || 'The model could not be loaded.';
            errorOverlay.classList.add('is-visible');
        },
    );

    function animate() {
        if (disposed) return;
        requestAnimationFrame(animate);
        controls.update();
        if (selectionOutline) selectionOutline.update();
        scene.background.set(getComputedStyle(viewport).backgroundColor);
        scene.fog.color.copy(scene.background);
        renderer.render(scene, camera);
    }
    animate();

    function dispose() {
        if (disposed) return;
        disposed = true;
        resizeObserver.disconnect();
        controls.dispose();
        renderer.dispose();
        cleanupViewerHeader();
        document.body.removeEventListener('htmx:beforeCleanupElement', cleanupHandler);
    }

    function cleanupHandler(event) {
        const cleanupElement = event.detail?.elt;
        if (cleanupElement === root || cleanupElement?.contains?.(root)) dispose();
    }
    document.body.addEventListener('htmx:beforeCleanupElement', cleanupHandler);

    return { clearSelection, dispose, fitModel };
}

if (typeof document !== 'undefined') {
    initializeViewer(document.getElementById('viewer-app'));
    document.body.addEventListener('htmx:afterSwap', (event) => {
        const swappedElement = event.detail?.elt;
        const root = swappedElement?.matches?.('#viewer-app')
            ? swappedElement
            : swappedElement?.querySelector?.('#viewer-app');
        initializeViewer(root);
    });
}