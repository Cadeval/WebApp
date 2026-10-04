import assert from 'node:assert/strict';
import test from 'node:test';
import * as THREE from 'three';
import { classifyMaterialFamily, createSurfaceTextures, createBoxUVGeometry, prepareModelSurfaces } from './viewer_surfaces.js';

function identifiedMesh(guid, material = new THREE.MeshStandardMaterial()) {
    const geometry = new THREE.BoxGeometry(2, 3, 4);
    geometry.deleteAttribute('uv');
    const mesh = new THREE.Mesh(geometry, material);
    mesh.userData.GlobalId = guid;
    return mesh;
}

test('only explicit unambiguous material names receive a visual family', () => {
    assert.equal(classifyMaterialFamily([{ name: 'Stahlbeton C30/37' }]), 'concrete');
    assert.equal(classifyMaterialFamily('Oak timber'), 'wood');
    assert.equal(classifyMaterialFamily(['Ziegel', 'Mauerziegel']), 'brick');
    assert.equal(classifyMaterialFamily('Gipsputz'), 'plaster');
    assert.equal(classifyMaterialFamily('Edelstahl'), 'metal');
    assert.equal(classifyMaterialFamily('Float glass'), 'glass');
    assert.equal(classifyMaterialFamily('Beton, Fertigteil'), 'concrete');
    assert.equal(classifyMaterialFamily('Holz, Brettschichtholz [+HOHE PRIO]'), 'wood');
    assert.equal(classifyMaterialFamily('Metall, Stahl'), 'metal');
    assert.equal(classifyMaterialFamily('Glas, Normalglas'), 'glass');
    assert.equal(classifyMaterialFamily('Verputz, Gips/Kunstharz'), 'plaster');
    assert.equal(classifyMaterialFamily('Gipskartonfeuerschutzplatte'), 'plaster');
    assert.equal(classifyMaterialFamily('MDF mitteldichte Faserplatte'), 'wood');
    assert.equal(classifyMaterialFamily('OSB Grobspanplatte'), 'wood');
    assert.equal(classifyMaterialFamily('BSH'), 'wood');
    assert.equal(classifyMaterialFamily('Gefällebeton'), 'concrete');
    assert.equal(classifyMaterialFamily('Verputz, Kalk HOLZ Fasade'), null);
    assert.equal(classifyMaterialFamily('Holzwollleichtplatte'), null);
    for (const material of ['DefaultMaterial', 'unknown concrete', 'Glass wool insulation', 'Holzfaserdämmung', 'Mauerwerk', 'Gypsum timber panel']) {
        assert.equal(classifyMaterialFamily(material), null, material);
    }
    assert.equal(classifyMaterialFamily(['concrete', 'steel']), null);
    assert.equal(classifyMaterialFamily(['concrete', 'unclassified']), null);
    assert.equal(classifyMaterialFamily([]), null);
});

test('procedural textures are deterministic, colour-correct and bounded with mipmaps', () => {
    const first = createSurfaceTextures('wood', 64);
    const second = createSurfaceTextures('wood');
    assert.deepEqual(first.map.image.data, second.map.image.data);
    assert.equal(first.map.image.width, 128);
    assert.equal(first.map.image.data.length, 128 * 128 * 4);
    assert.equal(first.map.colorSpace, THREE.SRGBColorSpace);
    assert.equal(first.bumpMap.colorSpace, THREE.NoColorSpace);
    assert.equal(first.roughnessMap.colorSpace, THREE.NoColorSpace);
    assert.equal(first.map.generateMipmaps, true);
    assert.equal(first.map.minFilter, THREE.LinearMipmapLinearFilter);
    assert.equal(first.map.wrapS, THREE.RepeatWrapping);
    assert.equal(first.map.anisotropy, 8);
    const brick = createSurfaceTextures('brick');
    assert.notDeepEqual(first.map.image.data, brick.map.image.data);
    assert.equal(createSurfaceTextures('glass'), null);
    assert.equal(createSurfaceTextures('unknown'), null);
    for (const textures of [first, second, brick]) Object.values(textures).forEach((texture) => texture.dispose());
});

test('box UVs preserve indexed topology, authored normals, groups and source geometry', () => {
    const source = new THREE.BoxGeometry(2, 3, 4);
    source.deleteAttribute('uv');
    const normal = source.getAttribute('normal').array.slice();
    const result = createBoxUVGeometry(source, new THREE.Matrix4().makeTranslation(10, 20, 30));
    assert.equal(result.index.count, source.index.count);
    assert.equal(result.getAttribute('position').count, source.getAttribute('position').count);
    assert.deepEqual(result.getAttribute('normal').array, normal);
    assert.deepEqual(result.groups, source.groups);
    assert.equal(source.getAttribute('uv'), undefined);
    const uv = result.getAttribute('uv');
    assert.ok([...uv.array].every(Number.isFinite));
    assert.ok(uv.getX(0) > 9);
    assert.ok(uv.getY(0) > 18);
    result.dispose();
    source.dispose();
});

test('shared cube corners split only at projection seams with no triangle expansion', () => {
    const source = new THREE.BufferGeometry();
    source.setAttribute('position', new THREE.Float32BufferAttribute([
        -1, -1, -1, 1, -1, -1, 1, 1, -1, -1, 1, -1,
        -1, -1, 1, 1, -1, 1, 1, 1, 1, -1, 1, 1,
    ], 3));
    source.setIndex([0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7,
        0, 1, 5, 0, 5, 4, 3, 7, 6, 3, 6, 2,
        0, 4, 7, 0, 7, 3, 1, 2, 6, 1, 6, 5]);
    const result = createBoxUVGeometry(source);
    assert.equal(result.index.count, 36);
    assert.equal(result.getAttribute('position').count, 24);
    assert.equal(source.getAttribute('position').count, 8);
    assert.equal(result.getAttribute('normal'), undefined);
    for (let offset = 0; offset < result.index.count; offset += 3) {
        const vertices = [0, 1, 2].map((index) => result.index.getX(offset + index));
        const uv = result.getAttribute('uv');
        const twiceArea = (uv.getX(vertices[1]) - uv.getX(vertices[0])) * (uv.getY(vertices[2]) - uv.getY(vertices[0]))
            - (uv.getY(vertices[1]) - uv.getY(vertices[0])) * (uv.getX(vertices[2]) - uv.getX(vertices[0]));
        assert.ok(Math.abs(twiceArea) > 0);
    }
    result.dispose();
    source.dispose();
});

test('projection rejects authored UVs, invalid geometry, animation and insufficient budgets', () => {
    const source = new THREE.BoxGeometry();
    assert.equal(createBoxUVGeometry(source), null);
    source.deleteAttribute('uv');
    assert.equal(createBoxUVGeometry(source, new THREE.Matrix4(), 1), null);
    source.morphAttributes.position = [source.getAttribute('position').clone()];
    assert.equal(createBoxUVGeometry(source), null);
    source.morphAttributes = {};
    source.getAttribute('position').setX(0, NaN);
    assert.equal(createBoxUVGeometry(source), null);
    source.dispose();
});

test('normalised interleaved attributes retain their raw values while seams split', () => {
    const source = new THREE.BufferGeometry();
    source.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0], 3));
    const interleaved = new THREE.InterleavedBuffer(new Uint8Array([99, 0, 128, 255, 99, 255, 0, 128, 99, 128, 255, 0]), 4);
    source.setAttribute('color', new THREE.InterleavedBufferAttribute(interleaved, 3, 1, true));
    const result = createBoxUVGeometry(source);
    assert.equal(result.getAttribute('color').normalized, true);
    assert.deepEqual(result.getAttribute('color').array, new Uint8Array([0, 128, 255, 255, 0, 128, 128, 255, 0]));
    result.dispose();
    source.dispose();
});

test('family textures share resources and preserve IFC colours and custom roughness', () => {
    const model = new THREE.Group();
    const first = identifiedMesh('one', new THREE.MeshStandardMaterial({ color: 0x884422 }));
    const second = identifiedMesh('two', new THREE.MeshStandardMaterial({ color: 0x335577, roughness: 0.41 }));
    model.add(first, second);
    const originalOne = first.material;
    const originalTwo = second.material;
    const geometryOne = first.geometry;
    const preparation = prepareModelSurfaces(model, {
        one: { materials: [{ name: 'Beton' }] }, two: { materials: [{ name: 'Concrete' }] },
    });
    assert.equal(preparation.counts.texturedMeshes, 2);
    assert.equal(preparation.counts.textureSets, 1);
    assert.equal(first.material.map, second.material.map);
    assert.ok(first.material.color.equals(originalOne.color));
    assert.ok(second.material.color.equals(originalTwo.color));
    assert.equal(second.material.roughness, 0.41);
    assert.equal(first.material.side, originalOne.side);
    assert.equal(first.material.polygonOffset, false);
    assert.notEqual(first.geometry, geometryOne);
    assert.equal(geometryOne.getAttribute('uv'), undefined);
    preparation.dispose();
    assert.equal(first.material, originalOne);
    assert.equal(second.material, originalTwo);
    assert.equal(first.geometry, geometryOne);
});

test('unknown or mixed assemblies and authored maps keep their appearance', () => {
    const model = new THREE.Group();
    const unknown = identifiedMesh('unknown', new THREE.MeshStandardMaterial({ color: 0xaabbcc }));
    const mixed = identifiedMesh('mixed');
    const authored = identifiedMesh('authored');
    const texture = new THREE.Texture();
    authored.material.map = texture;
    const originals = [unknown, mixed, authored].map((mesh) => mesh.material);
    model.add(unknown, mixed, authored);
    const preparation = prepareModelSurfaces(model, {
        unknown: { materials: [{ name: 'uncategorized' }] },
        mixed: { materials: [{ name: 'Timber' }, { name: 'Concrete' }] },
        authored: { materials: [{ name: 'Concrete' }] },
    });
    assert.deepEqual([unknown, mixed, authored].map((mesh) => mesh.material), originals);
    assert.equal(preparation.counts.texturedMeshes, 0);
    let textureDisposals = 0;
    texture.addEventListener('dispose', () => { textureDisposals += 1; });
    preparation.dispose();
    assert.equal(textureDisposals, 0);
    texture.dispose();
});

test('transparent IFC surfaces retain opacity, sidedness and picking while avoiding depth occlusion', () => {
    const model = new THREE.Group();
    const original = new THREE.MeshStandardMaterial({ color: 0xbcddee, transparent: true, opacity: 0.24, side: THREE.DoubleSide });
    const mesh = identifiedMesh('glass', original);
    model.add(mesh);
    const preparation = prepareModelSurfaces(model, { glass: { materials: [{ name: 'Glass' }] } });
    assert.equal(mesh.material.opacity, 0.24);
    assert.equal(mesh.material.transparent, true);
    assert.equal(mesh.material.side, THREE.DoubleSide);
    assert.equal(mesh.material.forceSinglePass, original.forceSinglePass);
    assert.equal(mesh.material.depthWrite, false);
    assert.equal(mesh.material.alphaHash, false);
    assert.equal(mesh.visible, true);
    assert.equal(original.depthWrite, true);
    preparation.dispose();
    assert.equal(mesh.material, original);
});

test('cleanup is idempotent and frees generated textures, geometry and materials exactly once', () => {
    const model = new THREE.Group();
    const mesh = identifiedMesh('wood');
    const originalMaterial = mesh.material;
    const originalGeometry = mesh.geometry;
    model.add(mesh);
    const preparation = prepareModelSurfaces(model, { wood: { materials: [{ name: 'Timber' }] } });
    const disposals = new Map();
    for (const resource of [mesh.geometry, mesh.material, mesh.material.map, mesh.material.bumpMap, mesh.material.roughnessMap]) {
        disposals.set(resource, 0);
        resource.addEventListener('dispose', () => disposals.set(resource, disposals.get(resource) + 1));
    }
    let originalDisposals = 0;
    originalMaterial.addEventListener('dispose', () => { originalDisposals += 1; });
    originalGeometry.addEventListener('dispose', () => { originalDisposals += 1; });
    preparation.dispose();
    preparation.dispose();
    assert.ok([...disposals.values()].every((count) => count === 1));
    assert.equal(originalDisposals, 0);
});

test('element identity on the product ancestor maps mesh materials without GLB name guesses', () => {
    const model = new THREE.Group();
    const product = new THREE.Group();
    product.userData.global_id = 'product-guid';
    const mesh = identifiedMesh('discarded');
    mesh.userData = {};
    mesh.material.name = 'Glass';
    mesh.material.transparent = true;
    mesh.material.opacity = 0.35;
    product.add(mesh);
    model.add(product);
    const preparation = prepareModelSurfaces(model, new Map([['product-guid', {
        materials: [{ name: 'Glass' }, { name: 'Aluminium' }],
    }]]));
    assert.equal(mesh.material.roughness, 0.14);
    assert.equal(mesh.material.opacity, 0.35);
    assert.equal(mesh.material.map, null);
    assert.equal(preparation.counts.unclassifiedMeshes, 0);
    preparation.dispose();
});

test('instances and skinned meshes without authored UVs do not get invalid world projections', () => {
    const model = new THREE.Group();
    const geometry = new THREE.BoxGeometry();
    geometry.deleteAttribute('uv');
    const mesh = new THREE.InstancedMesh(geometry, new THREE.MeshStandardMaterial(), 2);
    mesh.userData.GlobalId = 'instances';
    model.add(mesh);
    const preparation = prepareModelSurfaces(model, { instances: { materials: [{ name: 'Concrete' }] } });
    assert.equal(mesh.geometry, geometry);
    assert.equal(mesh.material.map, null);
    assert.equal(preparation.counts.skippedUV, 1);
    preparation.dispose();
});

test('disabling texture styling keeps transparency corrections while restoring IFC surface presets', () => {
    const model = new THREE.Group();
    const opaque = identifiedMesh('metal');
    const glass = identifiedMesh('glass', new THREE.MeshStandardMaterial({ transparent: true, opacity: 0.24 }));
    const opaqueMaterial = opaque.material;
    const glassMaterial = glass.material;
    const opaqueGeometry = opaque.geometry;
    model.add(opaque, glass);
    const elements = { metal: { materials: [{ name: 'Stahl' }] }, glass: { materials: [{ name: 'Glass' }] } };
    const styled = prepareModelSurfaces(model, elements);
    assert.ok(opaque.material.map);
    assert.equal(opaque.material.metalness, 0.82);
    styled.dispose();
    const unstyled = prepareModelSurfaces(model, elements, null, { textures: false });
    assert.equal(opaque.material, opaqueMaterial);
    assert.equal(opaque.geometry, opaqueGeometry);
    assert.equal(opaque.material.roughness, 1);
    assert.equal(opaque.material.metalness, 0);
    assert.notEqual(glass.material, glassMaterial);
    assert.equal(glass.material.opacity, 0.24);
    assert.equal(glass.material.depthWrite, false);
    assert.equal(glass.material.roughness, glassMaterial.roughness);
    assert.equal(unstyled.counts.textureSets, 0);
    assert.equal(unstyled.counts.texturedMeshes, 0);
    assert.equal(unstyled.counts.geometryBytes, 0);
    unstyled.dispose();
    assert.equal(glass.material, glassMaterial);
});

test('physical transmission corrects depth writing even at full opacity with no alpha map', () => {
    const model = new THREE.Group();
    const original = new THREE.MeshPhysicalMaterial({ transmission: 0.85, opacity: 1, roughness: 0.08 });
    const mesh = identifiedMesh('transmissive', original);
    model.add(mesh);
    const preparation = prepareModelSurfaces(model, {}, null, { textures: false });
    assert.equal(mesh.material.transmission, 0.85);
    assert.equal(mesh.material.opacity, 1);
    assert.equal(mesh.material.transparent, original.transparent);
    assert.equal(mesh.material.depthWrite, false);
    assert.equal(mesh.material.roughness, 0.08);
    assert.equal(preparation.counts.transparentMaterials, 1);
    assert.equal(preparation.counts.geometryBytes, 0);
    preparation.dispose();
    assert.equal(mesh.material, original);
    assert.equal(original.depthWrite, true);
});
