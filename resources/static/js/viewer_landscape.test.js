import assert from 'node:assert/strict';
import test from 'node:test';
import * as THREE from 'three';
import { landscapeLayout, createLandscape } from './viewer_landscape.js';

test('landscape retains a clear building footprint and tree crowns stay outside it', () => {
    for (const size of [[40,22,25],[3,10,50],[1,1,1]]) {
        const bounds = new THREE.Box3(new THREE.Vector3(8,-5,-25),new THREE.Vector3(8+size[0],-5+size[1],-25+size[2]));
        const before = bounds.clone(), { group, layout, dispose } = createLandscape(bounds);
        assert.deepEqual(bounds,before);
        assert.ok(layout.y < bounds.min.y);
        const positions = group.children[0].geometry.attributes.position;
        for (let i = 0; i < positions.count; i += 3) {
            const x = (positions.getX(i)+positions.getX(i+1)+positions.getX(i+2))/3;
            const z = (positions.getZ(i)+positions.getZ(i+1)+positions.getZ(i+2))/3;
            assert.ok(x < layout.inner.minX || x > layout.inner.maxX || z < layout.inner.minZ || z > layout.inner.maxZ);
        }
        for (const tree of layout.trees) {
            const dx = Math.max(layout.inner.minX-tree.x,0,tree.x-layout.inner.maxX);
            const dz = Math.max(layout.inner.minZ-tree.z,0,tree.z-layout.inner.maxZ);
            assert.ok(Math.hypot(dx,dz) > tree.radius * 1.25);
        }
        dispose();
    }
});

test('vegetation uses a fixed small instanced draw budget and never changes IFC model quantities', () => {
    const model = new THREE.Mesh(new THREE.BoxGeometry(35,18,30),new THREE.MeshStandardMaterial());
    model.userData.GlobalId = 'unchanged-ifc-guid';
    const positions = model.geometry.attributes.position.array.slice();
    const scenery = createLandscape(new THREE.Box3().setFromObject(model));
    assert.equal(scenery.group.children.length,4);
    assert.equal(scenery.group.children.filter(mesh=>mesh.isInstancedMesh).length,3);
    assert.equal(scenery.group.children[2].count,48);
    assert.ok(scenery.group.children.slice(1).every(mesh=>!mesh.castShadow));
    assert.deepEqual(model.geometry.attributes.position.array,positions);
    assert.equal(model.userData.GlobalId,'unchanged-ifc-guid');
    scenery.dispose(); model.geometry.dispose(); model.material.dispose();
});

test('preview landscaping releases its shared GPU resources once on replacement', () => {
    const scene = new THREE.Scene();
    const scenery = createLandscape(new THREE.Box3(new THREE.Vector3(-20,-8,-15),new THREE.Vector3(20,8,15)));
    scene.add(scenery.group);
    const resources = new Set();
    scenery.group.traverse(mesh=>{
        if (mesh.isInstancedMesh) resources.add(mesh);
        if (mesh.geometry) resources.add(mesh.geometry);
        if (mesh.material) { resources.add(mesh.material); if(mesh.material.map)resources.add(mesh.material.map); }
    });
    let count = 0;
    for (const resource of resources)resource.addEventListener('dispose',()=>count++);
    scenery.dispose(); scenery.dispose();
    assert.equal(count,resources.size); assert.equal(scene.children.length,0);
});

test('empty or nonfinite bounds create no scenery rather than corrupting the scene', () => {
    for (const bounds of [null,new THREE.Box3(),new THREE.Box3(new THREE.Vector3(NaN,0,0),new THREE.Vector3(1,1,1))]) {
        assert.equal(landscapeLayout(bounds),null);
        const scenery=createLandscape(bounds); assert.equal(scenery.group.children.length,0); scenery.dispose();
    }
});
