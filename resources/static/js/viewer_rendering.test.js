import assert from 'node:assert/strict';
import test from 'node:test';
import * as THREE from 'three';
import { fitShadowToBounds, recenterModel, updateCameraClipping, fitCameraToBounds, createRenderResources, disposeModelResources } from './viewer_rendering.js';

test('renderer startup releases partial resources if context or environment initialization fails', () => {
    let rendererDisposed = 0, environmentDisposed = 0;
    const canvas = { getContext: () => ({ getExtension: () => null }) };
    const rendererFactory = () => ({ shadowMap: {}, setPixelRatio() {}, dispose() { rendererDisposed++; } });
    assert.throws(() => createRenderResources(canvas, {}, { rendererFactory, environmentFactory: () => { throw new Error('GPU allocation failed'); } }), /GPU allocation failed/);
    assert.equal(rendererDisposed, 1);
    const scene = { set environment(value) { throw new Error('Environment failed'); } };
    assert.throws(() => createRenderResources(canvas, scene, { rendererFactory, environmentFactory: () => ({ texture: {}, dispose() { environmentDisposed++; } }) }), /Environment failed/);
    assert.equal(rendererDisposed, 2); assert.equal(environmentDisposed, 1);
    assert.throws(() => createRenderResources({ getContext() { throw new Error('WebGL unavailable'); } }, {}), /WebGL unavailable/);
    assert.equal(rendererDisposed, 2);
});

test('camera fitting contains a wide house in both landscape and portrait viewports', () => {
    const bounds = new THREE.Box3(new THREE.Vector3(-30, -16, -29), new THREE.Vector3(30, 16, 29));
    for (const aspect of [2, 0.5]) {
        const camera = new THREE.PerspectiveCamera(45, aspect, 0.01, 1000);
        fitCameraToBounds(camera, bounds); camera.updateMatrixWorld();
        for (const x of [-30, 30]) for (const y of [-16, 16]) for (const z of [-29, 29]) {
            const projected = new THREE.Vector3(x, y, z).project(camera);
            assert.ok(Math.abs(projected.x) < 1 && Math.abs(projected.y) < 1);
        }
    }
});

test('fitted shadows contain every corner of translated wide and tall model bounds', () => {
    for (const bounds of [
        new THREE.Box3(new THREE.Vector3(-14, -5, -59), new THREE.Vector3(43, 27, -1)),
        new THREE.Box3(new THREE.Vector3(400000, 20000, -130000), new THREE.Vector3(400063, 20070, -129981)),
    ]) {
        const light = new THREE.DirectionalLight(); light.shadow.mapSize.set(2048, 2048);
        fitShadowToBounds(light, bounds);
        light.shadow.updateMatrices(light);
        const camera = light.shadow.camera;
        for (const x of [bounds.min.x, bounds.max.x]) for (const y of [bounds.min.y, bounds.max.y]) for (const z of [bounds.min.z, bounds.max.z]) {
            const projected = new THREE.Vector3(x, y, z).project(camera);
            assert.ok(projected.toArray().every(value => value > -1 && value < 1), projected.toArray().join(','));
        }
        assert.ok(light.shadow.normalBias > 0 && light.shadow.normalBias < 0.1);
        assert.ok(light.shadow.bias < 0);
    }
});

test('recentering preserves source buffers, child transforms, dimensions and IFC identity', () => {
    const scene = new THREE.Group(), mesh = new THREE.Mesh(new THREE.BoxGeometry(2, 3, 4), new THREE.MeshStandardMaterial());
    scene.add(mesh); mesh.position.set(500000, 30, -100000); mesh.userData.GlobalId = 'source-guid';
    const positions = mesh.geometry.attributes.position.array.slice(), transform = mesh.matrix.clone();
    mesh.updateMatrix(); transform.copy(mesh.matrix);
    const size = new THREE.Box3().setFromObject(scene).getSize(new THREE.Vector3());
    const bounds = recenterModel(scene);
    assert.ok(bounds.getCenter(new THREE.Vector3()).length() < 1e-6);
    assert.deepEqual(bounds.getSize(new THREE.Vector3()), size);
    assert.deepEqual(mesh.geometry.attributes.position.array, positions);
    assert.deepEqual(mesh.matrix, transform);
    assert.equal(mesh.userData.GlobalId, 'source-guid');
    disposeModelResources(scene);
});

test('dynamic clipping contains model bounds when orbit target pans and user zooms', () => {
    const bounds = new THREE.Box3(new THREE.Vector3(-30, -20, -30), new THREE.Vector3(30, 20, 30));
    const camera = new THREE.PerspectiveCamera(), controls = { target: new THREE.Vector3(120, 0, 0) };
    for (const position of [new THREE.Vector3(130, 5, 10), new THREE.Vector3(0, 0, 0.01), new THREE.Vector3(1000, 100, 100)]) {
        camera.position.copy(position); updateCameraClipping(camera, controls, bounds);
        assert.ok(camera.near > 0 && camera.near < 0.1);
        for (const x of [-30, 30]) for (const y of [-20, 20]) for (const z of [-30, 30]) assert.ok(camera.far > camera.position.distanceTo(new THREE.Vector3(x, y, z)));
        assert.ok(camera.far < 1500);
    }
});

test('shared GLB resources are disposed once across repeated primitives', () => {
    const scene = new THREE.Group(), geometry = new THREE.BoxGeometry(), material = new THREE.MeshStandardMaterial(), texture = new THREE.Texture();
    material.map = texture;
    scene.add(new THREE.Mesh(geometry, material), new THREE.Mesh(geometry, material));
    const counts = { geometry: 0, material: 0, texture: 0 };
    geometry.addEventListener('dispose', () => counts.geometry++);
    material.addEventListener('dispose', () => counts.material++);
    texture.addEventListener('dispose', () => counts.texture++);
    disposeModelResources(scene);
    assert.deepEqual(counts, { geometry: 1, material: 1, texture: 1 });
});
