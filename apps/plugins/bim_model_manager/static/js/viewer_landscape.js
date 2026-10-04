import * as THREE from 'three';

// Display-only scenery. These objects never enter IFC selection or quantities.
export function landscapeLayout(bounds) {
    if (!bounds || bounds.isEmpty()) return null;
    const coordinates = [...bounds.min.toArray(), ...bounds.max.toArray()];
    if (!coordinates.every(Number.isFinite)) return null;
    const size = bounds.getSize(new THREE.Vector3());
    const span = Math.max(size.x, size.z, 1);
    const margin = Math.max(span * 0.34, 2);
    const clearance = Math.max(span * 0.04, 0.25);
    const inner = { minX: bounds.min.x - clearance, maxX: bounds.max.x + clearance,
                    minZ: bounds.min.z - clearance, maxZ: bounds.max.z + clearance };
    const outer = { minX: bounds.min.x - margin, maxX: bounds.max.x + margin,
                    minZ: bounds.min.z - margin, maxZ: bounds.max.z + margin };
    const y = bounds.min.y - Math.max(Math.max(size.x, size.y, size.z) * 0.003, 0.015);
    const trees = [];
    for (let side = 0; side < 4; side++) {
        for (let i = 0; i < 4; i++) {
            const t = (i + 0.55) / 4.1;
            const alongX = outer.minX + (outer.maxX - outer.minX) * t;
            const alongZ = outer.minZ + (outer.maxZ - outer.minZ) * t;
            const x = side === 0 ? bounds.min.x - margin * 0.78 : side === 1 ? bounds.max.x + margin * 0.78 : alongX;
            const z = side === 2 ? bounds.min.z - margin * 0.78 : side === 3 ? bounds.max.z + margin * 0.78 : alongZ;
            const height = span * (0.12 + (i % 3) * 0.012);
            const radius = height * 0.3;
            trees.push({ x, z, y, height, radius });
        }
    }
    return { span, margin, clearance, inner, outer, y, trees };
}

function lawnGeometry(layout) {
    const positions = [], uv = [];
    const { inner: i, outer: o, y, span } = layout;
    const rectangles = [
        [o.minX, o.maxX, o.minZ, i.minZ], [o.minX, o.maxX, i.maxZ, o.maxZ],
        [o.minX, i.minX, i.minZ, i.maxZ], [i.maxX, o.maxX, i.minZ, i.maxZ],
    ];
    for (const [x1, x2, z1, z2] of rectangles) {
        for (const [x, z] of [[x1,z1],[x1,z2],[x2,z2],[x1,z1],[x2,z2],[x2,z1]]) {
            positions.push(x, y, z); uv.push(x / Math.max(span / 12, 0.5), z / Math.max(span / 12, 0.5));
        }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
    geometry.computeVertexNormals();
    return geometry;
}

function grassTexture() {
    const pixels = new Uint8Array(32 * 32 * 4);
    let seed = 112358;
    for (let i = 0; i < 32 * 32; i++) {
        seed = (1664525 * seed + 1013904223) >>> 0;
        const noise = (seed >>> 24) / 255 * 18 - 9;
        pixels.set([111 + noise, 132 + noise, 82 + noise, 255], i * 4);
    }
    const texture = new THREE.DataTexture(pixels, 32, 32, THREE.RGBAFormat);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
    texture.magFilter = THREE.LinearFilter;
    texture.minFilter = THREE.LinearMipmapLinearFilter;
    texture.generateMipmaps = true; texture.needsUpdate = true;
    return texture;
}

export function createLandscape(bounds) {
    const group = new THREE.Group(); group.name = 'Preview landscaping';
    group.userData.previewLandscape = true;
    const layout = landscapeLayout(bounds);
    const geometries = new Set(), materials = new Set(), textures = new Set();
    const own = resource => { (resource.isTexture ? textures : resource.isMaterial ? materials : geometries).add(resource); return resource; };
    let disposed = false;
    function dispose() {
        if (disposed) return;
        disposed = true; group.removeFromParent();
        group.traverse(child => { if (child.isInstancedMesh) child.dispose(); });
        for (const resource of [...textures, ...materials, ...geometries]) resource.dispose();
        group.clear();
    }
    if (!layout) return { group, layout, dispose };
    const texture = own(grassTexture());
    const lawn = new THREE.Mesh(own(lawnGeometry(layout)), own(new THREE.MeshStandardMaterial({
        map: texture, roughness: 1, metalness: 0, color: 0xe6e9df,
    })));
    lawn.receiveShadow = true; group.add(lawn);
    const trunks = new THREE.InstancedMesh(own(new THREE.CylinderGeometry(0.065,0.09,1,7)),
        own(new THREE.MeshStandardMaterial({ color: 0x76644e, roughness: 1 })), layout.trees.length);
    const crowns = new THREE.InstancedMesh(own(new THREE.IcosahedronGeometry(1,1)),
        own(new THREE.MeshStandardMaterial({ color: 0x71805b, roughness: 0.95, flatShading: false })), layout.trees.length * 3);
    const shadow = new THREE.InstancedMesh(own(new THREE.CircleGeometry(1,16)),
        own(new THREE.MeshBasicMaterial({ color: 0x39432e, opacity: 0.12, transparent: true, depthWrite: false })), layout.trees.length);
    const transform = new THREE.Object3D();
    for (let index = 0; index < layout.trees.length; index++) {
        const tree = layout.trees[index];
        transform.position.set(tree.x,tree.y + tree.height * 0.34,tree.z);
        transform.rotation.set(0,0,0); transform.scale.set(tree.height,tree.height * 0.68,tree.height);
        transform.updateMatrix(); trunks.setMatrixAt(index,transform.matrix);
        for (let lobe = 0; lobe < 3; lobe++) {
            const angle = lobe * Math.PI * 2 / 3 + index;
            transform.position.set(tree.x + Math.cos(angle) * tree.radius * 0.24,
                tree.y + tree.height * (0.7 + lobe * 0.045),tree.z + Math.sin(angle) * tree.radius * 0.24);
            transform.rotation.set(0,index * 0.61 + lobe,0);
            transform.scale.set(tree.radius,tree.radius * (1.25 - lobe * 0.1),tree.radius);
            transform.updateMatrix(); crowns.setMatrixAt(index * 3 + lobe,transform.matrix);
            crowns.setColorAt(index * 3 + lobe,new THREE.Color().setHSL(0.23 + index % 3 * 0.012,0.19,0.34 + lobe * 0.025));
        }
        transform.position.set(tree.x,tree.y + Math.max(layout.span * 0.0001,0.001),tree.z);
        transform.rotation.set(-Math.PI / 2,0,0); transform.scale.set(tree.radius * 1.15,tree.radius * 0.95,1);
        transform.updateMatrix(); shadow.setMatrixAt(index,transform.matrix);
    }
    for (const mesh of [trunks,crowns,shadow]) {
        mesh.instanceMatrix.needsUpdate = true; mesh.computeBoundingBox(); mesh.computeBoundingSphere();
        // Static instanced scenery uses soft contact patches, avoiding 64 extra shadow draws.
        mesh.castShadow = false; group.add(mesh);
    }
    group.updateMatrixWorld(true);
    return { group, layout, dispose };
}
