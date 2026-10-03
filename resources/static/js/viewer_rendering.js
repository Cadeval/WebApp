import * as THREE from 'three';

export function fitShadowToBounds(light, bounds) {
    if (!bounds || bounds.isEmpty()) return;
    const center = bounds.getCenter(new THREE.Vector3());
    const span = Math.max(...bounds.getSize(new THREE.Vector3()).toArray(), 1);
    light.position.copy(center).add(new THREE.Vector3(1, 1.6, 1).normalize().multiplyScalar(span * 2));
    light.target.position.copy(center);
    light.updateMatrixWorld(true);
    light.target.updateMatrixWorld(true);
    const camera = light.shadow.camera;
    camera.position.copy(light.position);
    camera.lookAt(center);
    camera.updateMatrixWorld(true);
    const projected = new THREE.Box3();
    for (const x of [bounds.min.x, bounds.max.x])
        for (const y of [bounds.min.y, bounds.max.y])
            for (const z of [bounds.min.z, bounds.max.z])
                projected.expandByPoint(new THREE.Vector3(x, y, z).applyMatrix4(camera.matrixWorldInverse));
    const margin = span * 0.04;
    camera.left = projected.min.x - margin;
    camera.right = projected.max.x + margin;
    camera.bottom = projected.min.y - margin;
    camera.top = projected.max.y + margin;
    camera.near = Math.max(0.01, -projected.max.z - margin);
    camera.far = Math.max(camera.near + 1, -projected.min.z + margin);
    camera.updateProjectionMatrix();
    // Half a shadow texel offsets self-shadow acne without floating surfaces.
    light.shadow.normalBias = Math.max(camera.right - camera.left, camera.top - camera.bottom) / light.shadow.mapSize.x * 0.5;
    light.shadow.bias = -0.00004;
}

export function recenterModel(model) {
    const original = new THREE.Box3().setFromObject(model);
    if (original.isEmpty()) return original;
    // Display translation only: retain geometry, IFC identity and source coordinates.
    model.position.sub(original.getCenter(new THREE.Vector3()));
    model.updateMatrixWorld(true);
    return new THREE.Box3().setFromObject(model);
}

export function updateCameraClipping(camera, controls, bounds) {
    if (!bounds || bounds.isEmpty()) return;
    const center = bounds.getCenter(new THREE.Vector3());
    const radius = Math.max(bounds.getSize(new THREE.Vector3()).length() / 2, 0.1);
    const targetDistance = camera.position.distanceTo(controls.target);
    camera.near = Math.max(0.002, Math.min(radius * 0.001, targetDistance * 0.002));
    camera.far = Math.max(radius * 2, camera.position.distanceTo(center) + radius * 2);
    camera.updateProjectionMatrix();
}

export function fitCameraToBounds(camera, bounds, direction = new THREE.Vector3(0.7, 0.55, 0.7)) {
    const center = bounds.getCenter(new THREE.Vector3());
    const viewDirection = direction.clone().normalize();
    const rotation = new THREE.Matrix4().lookAt(viewDirection, new THREE.Vector3(), camera.up).invert();
    const tangentY = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    const tangentX = tangentY * camera.aspect;
    let distance = 0.1;
    for (const x of [bounds.min.x, bounds.max.x])
        for (const y of [bounds.min.y, bounds.max.y])
            for (const z of [bounds.min.z, bounds.max.z]) {
                const point = new THREE.Vector3(x, y, z).sub(center).applyMatrix4(rotation);
                distance = Math.max(distance, point.z + Math.abs(point.x) / tangentX, point.z + Math.abs(point.y) / tangentY);
            }
    camera.position.copy(center).addScaledVector(viewDirection, distance * 1.12);
    camera.lookAt(center);
    return center;
}

export function createStudioEnvironment(renderer) {
    const studio = new THREE.Scene();
    studio.background = new THREE.Color(0xaaaaaa);
    const resources = [];
    for (const [position, color, scale] of [
        [[0, 5, 0], 0xffffff, [10, 1, 10]],
        [[-5, 1, 0], 0xcccccc, [1, 8, 8]],
        [[5, 1, 0], 0x777777, [1, 8, 8]],
    ]) {
        const geometry = new THREE.BoxGeometry(...scale);
        const material = new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide });
        const mesh = new THREE.Mesh(geometry, material);
        mesh.position.set(...position);
        studio.add(mesh);
        resources.push(geometry, material);
    }
    let generator;
    try { generator = new THREE.PMREMGenerator(renderer); return generator.fromScene(studio, 0.04); }
    finally { generator?.dispose(); for (const resource of resources) resource.dispose(); }
}

export function createRenderResources(canvas, scene, {
    rendererFactory = options => new THREE.WebGLRenderer(options), environmentFactory = createStudioEnvironment,
} = {}) {
    let renderer, environment;
    try {
        const context = canvas.getContext('webgl2', { antialias: true });
        renderer = rendererFactory({ canvas, context, antialias: true,
            reversedDepthBuffer: Boolean(context?.getExtension('EXT_clip_control')) });
        renderer.setPixelRatio(Math.min(globalThis.devicePixelRatio || 1, 2));
        renderer.shadowMap.enabled = true;
        renderer.shadowMap.type = THREE.PCFShadowMap;
        renderer.shadowMap.autoUpdate = false;
        renderer.outputColorSpace = THREE.SRGBColorSpace;
        renderer.toneMapping = THREE.ACESFilmicToneMapping;
        renderer.toneMappingExposure = 1.05;
        environment = environmentFactory(renderer);
        scene.environment = environment.texture;
        return { renderer, environment };
    } catch (error) {
        environment?.dispose(); renderer?.dispose();
        throw error;
    }
}

export function disposeModelResources(model) {
    const geometries = new Set(), materials = new Set(), textures = new Set();
    model?.traverse(child => {
        if (child.geometry) geometries.add(child.geometry);
        for (const material of Array.isArray(child.material) ? child.material : [child.material]) {
            if (!material) continue;
            materials.add(material);
            for (const value of Object.values(material)) if (value?.isTexture) textures.add(value);
        }
    });
    for (const resource of [...textures, ...materials, ...geometries]) resource.dispose();
}
