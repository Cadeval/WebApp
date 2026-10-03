import * as THREE from 'three';

// These are presentation presets, not measured properties or assessment data.
const SURFACES = {
    concrete: { roughness: 0.86, tile: [0.8, 0.8], bump: 0.0008 },
    wood: { roughness: 0.62, tile: [0.65, 1.8], bump: 0.0005 },
    brick: { roughness: 0.9, tile: [0.48, 0.3], bump: 0.001 },
    plaster: { roughness: 0.94, tile: [0.6, 0.6], bump: 0.00035 },
    metal: { roughness: 0.32, tile: [0.6, 0.6], bump: 0.00008 },
    glass: { roughness: 0.14 },
};
const TEXTURE_SIZE = 128;
const GEOMETRY_BUDGET = 32 * 1024 * 1024;
const MAX_PROJECTED_VERTICES = 250_000;
const AUTHORED_MAPS = [
    'map', 'normalMap', 'bumpMap', 'roughnessMap', 'metalnessMap',
    'displacementMap', 'alphaMap', 'aoMap', 'emissiveMap', 'lightMap',
];

function normalizedName(value) {
    return String(value ?? '').normalize('NFKC').toLowerCase()
        .replace(/[_\-/]+/g, ' ').replace(/\s+/g, ' ').trim();
}

function nameFamily(value) {
    const name = normalizedName(value);
    if (!name || /\b(unknown|unbekannt|undefined|default|missing)\b/.test(name)) return null;
    // Fibre insulation and composite products are not solid wood or glass.
    if (/\b(wool|fiber|fibre|insulation|dämmung|mineralwolle|glaswolle|holzfaser)\b/.test(name)
        || /(glasfaser|glaswolle|holzfaser|dämm)/.test(name)) return null;
    const matches = [
        ['concrete', /\b(concrete|beton|stahlbeton|leichtbeton|porenbeton|betonfertigteil|gefällebeton)\b/],
        ['wood', /\b(wood|timber|hardwood|softwood|plywood|clt|bsh|osb|mdf|holz|massivholz|brettsperrholz|fichte|eiche|buche)\b/],
        ['brick', /\b(brick|bricks|brickwork|ziegel|mauerziegel|hochlochziegel|ziegelmauerwerk)\b/],
        ['plaster', /\b(plaster|gypsum|gips|gipsputz|gipskarton|gipskartonplatte|gipskartonfeuerschutzplatte|putz|verputz)\b/],
        ['metal', /\b(metal|metall|steel|aluminium|aluminum|iron|copper|zinc|stahl|edelstahl|eisen|kupfer|zink)\b/],
        ['glass', /\b(glass|glazing|glas|verglasung)\b/],
    ].filter(([, pattern]) => pattern.test(name));
    return matches.length === 1 ? matches[0][0] : null;
}

/** A mixed assembly keeps its IFC appearance rather than guessing its exposed layer. */
export function classifyMaterialFamily(materials) {
    const entries = Array.isArray(materials) ? materials : [materials];
    const names = entries.map((entry) => typeof entry === 'string' ? entry : entry?.name);
    if (!names.length) return null;
    const families = names.map(nameFamily);
    return families.every((family) => family && family === families[0]) ? families[0] : null;
}

function noise(x, y, seed = 1) {
    let value = Math.imul(x + 11, 374761393) ^ Math.imul(y + 17, 668265263) ^ seed;
    value = Math.imul(value ^ (value >>> 13), 1274126177);
    return ((value ^ (value >>> 16)) >>> 0) / 4294967295;
}

/** Seamless, deterministic neutral detail multiplies the original IFC colour. */
export function createSurfaceTextures(family, anisotropy = 1) {
    const profile = SURFACES[family];
    if (!profile?.tile) return null;
    const albedo = new Uint8Array(TEXTURE_SIZE * TEXTURE_SIZE * 4);
    const roughness = new Uint8Array(albedo.length);
    const height = new Uint8Array(albedo.length);
    for (let y = 0; y < TEXTURE_SIZE; y += 1) {
        for (let x = 0; x < TEXTURE_SIZE; x += 1) {
            const u = x / TEXTURE_SIZE;
            const v = y / TEXTURE_SIZE;
            const grain = noise(x, y) - 0.5;
            const broad = Math.sin(u * Math.PI * 8) * Math.cos(v * Math.PI * 6);
            let colour = 249 + grain * 8;
            let relief = 128 + grain * 20;
            let rough = 243 + grain * 10;
            if (family === 'concrete') {
                colour += broad * 2;
                relief += broad * 4;
            } else if (family === 'wood') {
                const rings = Math.sin(u * Math.PI * 40 + Math.sin(v * Math.PI * 4) * 1.4);
                colour = 244 + rings * 7 + grain * 3;
                relief = 128 + rings * 25;
                rough = 239 + rings * 7;
            } else if (family === 'brick') {
                const row = Math.floor(v * 2);
                const brickU = (u * 2 + (row % 2) * 0.5) % 1;
                const mortar = brickU < 0.055 || (v * 2) % 1 < 0.07;
                colour = mortar ? 222 : 249 + broad * 2 + grain * 5;
                relief = mortar ? 75 : 158 + grain * 8;
                rough = mortar ? 253 : 244 + grain * 6;
            } else if (family === 'plaster') {
                colour = 251 + grain * 4;
                relief = 128 + grain * 12;
            } else if (family === 'metal') {
                const brush = Math.sin(v * Math.PI * 32);
                colour = 252 + grain * 2;
                relief = 128 + brush * 8;
                rough = 225 + brush * 13;
            }
            const offset = (y * TEXTURE_SIZE + x) * 4;
            for (const [data, value] of [[albedo, colour], [roughness, rough], [height, relief]]) {
                const byte = Math.max(0, Math.min(255, Math.round(value)));
                data.set([byte, byte, byte, 255], offset);
            }
        }
    }
    function texture(data, colourSpace) {
        const result = new THREE.DataTexture(data, TEXTURE_SIZE, TEXTURE_SIZE, THREE.RGBAFormat);
        result.colorSpace = colourSpace;
        result.wrapS = result.wrapT = THREE.RepeatWrapping;
        result.magFilter = THREE.LinearFilter;
        result.minFilter = THREE.LinearMipmapLinearFilter;
        result.generateMipmaps = true;
        result.anisotropy = Number.isFinite(anisotropy) ? Math.max(1, Math.min(8, anisotropy)) : 1;
        result.repeat.set(1 / profile.tile[0], 1 / profile.tile[1]);
        result.needsUpdate = true;
        return result;
    }
    return {
        map: texture(albedo, THREE.SRGBColorSpace),
        roughnessMap: texture(roughness, THREE.NoColorSpace),
        bumpMap: texture(height, THREE.NoColorSpace),
    };
}

function rawComponent(attribute, index, component) {
    if (attribute.isInterleavedBufferAttribute) {
        return attribute.data.array[index * attribute.data.stride + attribute.offset + component];
    }
    return attribute.array[index * attribute.itemSize + component];
}

/**
 * Split vertices only where box projections meet, retaining indexing and groups.
 * Limits bound extra memory; animated geometry retains its authored surface.
 */
export function createBoxUVGeometry(source, worldMatrix = new THREE.Matrix4(), maxBytes = GEOMETRY_BUDGET) {
    const position = source?.getAttribute?.('position');
    if (!position || position.itemSize !== 3 || source.getAttribute('uv')
        || position.count > MAX_PROJECTED_VERTICES
        || Object.values(source.morphAttributes ?? {}).some((entries) => entries.length)) return null;
    const attributes = Object.entries(source.attributes);
    if (attributes.some(([, attribute]) => !(attribute.array || attribute.data?.array)
        || attribute.count !== position.count || attribute.isInstancedBufferAttribute)) return null;
    const indexCount = source.index?.count ?? position.count;
    if (!indexCount || indexCount % 3 || indexCount > MAX_PROJECTED_VERTICES * 6) return null;

    const mapping = new Map();
    const vertices = [];
    const indices = [];
    const a = new THREE.Vector3();
    const b = new THREE.Vector3();
    const c = new THREE.Vector3();
    const edge = new THREE.Vector3();
    const normal = new THREE.Vector3();
    for (let offset = 0; offset < indexCount; offset += 3) {
        const originals = [0, 1, 2].map((step) => source.index?.getX(offset + step) ?? offset + step);
        if (originals.some((index) => !Number.isInteger(index) || index < 0 || index >= position.count)) return null;
        a.fromBufferAttribute(position, originals[0]).applyMatrix4(worldMatrix);
        b.fromBufferAttribute(position, originals[1]).applyMatrix4(worldMatrix);
        c.fromBufferAttribute(position, originals[2]).applyMatrix4(worldMatrix);
        if ([a, b, c].some((point) => ![point.x, point.y, point.z].every(Number.isFinite))) return null;
        normal.subVectors(b, a).cross(edge.subVectors(c, a));
        const components = [Math.abs(normal.x), Math.abs(normal.y), Math.abs(normal.z)];
        const axis = components.indexOf(Math.max(...components));
        for (const original of originals) {
            const key = original * 3 + axis;
            if (!mapping.has(key)) {
                mapping.set(key, vertices.length);
                vertices.push({ original, axis });
            }
            indices.push(mapping.get(key));
        }
        if (vertices.length > MAX_PROJECTED_VERTICES) return null;
    }
    const bytes = vertices.length * (8 + attributes.reduce((total, [, attribute]) => {
        const array = attribute.array ?? attribute.data.array;
        return total + attribute.itemSize * array.BYTES_PER_ELEMENT;
    }, 0)) + indices.length * (vertices.length > 65535 ? 4 : 2);
    if (bytes > maxBytes) return null;

    const result = new THREE.BufferGeometry();
    result.name = source.name;
    result.groups = source.groups.map((group) => ({ ...group }));
    result.drawRange = { ...source.drawRange };
    result.boundingBox = source.boundingBox?.clone() ?? null;
    result.boundingSphere = source.boundingSphere?.clone() ?? null;
    result.userData = { ...source.userData, viewerSurfaceBytes: bytes };
    for (const [name, attribute] of attributes) {
        const originalArray = attribute.array ?? attribute.data.array;
        const array = new originalArray.constructor(vertices.length * attribute.itemSize);
        vertices.forEach(({ original }, target) => {
            for (let component = 0; component < attribute.itemSize; component += 1) {
                array[target * attribute.itemSize + component] = rawComponent(attribute, original, component);
            }
        });
        const copy = attribute.isFloat16BufferAttribute
            ? new THREE.Float16BufferAttribute(array, attribute.itemSize, attribute.normalized)
            : new THREE.BufferAttribute(array, attribute.itemSize, attribute.normalized);
        copy.name = attribute.name;
        copy.gpuType = attribute.gpuType;
        result.setAttribute(name, copy);
    }
    result.setIndex(indices);
    const uv = new Float32Array(vertices.length * 2);
    vertices.forEach(({ original, axis }, target) => {
        a.fromBufferAttribute(position, original).applyMatrix4(worldMatrix);
        uv[target * 2] = axis === 0 ? a.z : a.x;
        uv[target * 2 + 1] = axis === 1 ? a.z : a.y;
    });
    result.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
    return result;
}

function elementForMesh(mesh, model, elements) {
    let owner = mesh;
    while (owner && owner !== model) {
        const identity = Object.entries(owner.userData ?? {}).find(([key]) =>
            ['globalid', 'global_id', 'guid'].includes(key.toLowerCase()),
        )?.[1];
        if (identity != null) return elements instanceof Map ? elements.get(String(identity)) : elements?.[String(identity)];
        owner = owner.parent;
    }
    return null;
}

function familyForMaterial(material, materials) {
    const exact = material.name && materials?.find((entry) =>
        normalizedName(typeof entry === 'string' ? entry : entry.name) === normalizedName(material.name),
    );
    return classifyMaterialFamily(exact ? [exact] : materials ?? []);
}

/**
 * Applies bounded visual detail; calling dispose restores the original model.
 * textures:false keeps transparency corrections without changing IFC styling.
 */
export function prepareModelSurfaces(model, elements = {}, renderer = null, options = {}) {
    const withTextures = options.textures !== false;
    const originals = [];
    const textures = new Map();
    const generatedMaterials = new Set();
    const generatedGeometries = new Set();
    const materialCache = new WeakMap();
    const geometryCache = new WeakMap();
    const counts = { texturedMeshes: 0, transparentMaterials: 0, preservedAuthored: 0,
        unclassifiedMeshes: 0, skippedUV: 0, textureSets: 0, geometryBytes: 0 };
    const anisotropy = renderer?.capabilities?.getMaxAnisotropy?.() ?? 1;
    model.updateMatrixWorld(true);

    function preparedMaterial(original, family, useTexture) {
        const translucent = (original.transparent && (original.opacity < 1 || original.alphaMap || original.map))
            || original.transmission > 0;
        const supported = original.isMeshStandardMaterial;
        const authored = AUTHORED_MAPS.some((key) => original[key]);
        if (authored) counts.preservedAuthored += 1;
        if (!(withTextures && supported && family && !authored) && !translucent) return original;
        const key = `${family ?? ''}:${useTexture && !authored}`;
        let cache = materialCache.get(original);
        if (!cache) { cache = new Map(); materialCache.set(original, cache); }
        if (cache.has(key)) return cache.get(key);
        const material = original.clone();
        if (translucent) {
            material.depthWrite = false;
            counts.transparentMaterials += 1;
        }
        if (withTextures && supported && family && !authored) {
            const profile = SURFACES[family];
            // IFC exporters commonly leave roughness at its default of 1.
            if (material.roughness === 1) material.roughness = profile.roughness;
            if (family === 'metal' && material.metalness === 0) material.metalness = 0.82;
            material.dithering = true;
            if (useTexture && profile.tile) {
                if (!textures.has(family)) textures.set(family, createSurfaceTextures(family, anisotropy));
                Object.assign(material, textures.get(family));
                material.bumpScale = profile.bump;
            }
        }
        material.needsUpdate = true;
        generatedMaterials.add(material);
        cache.set(key, material);
        return material;
    }

    model.traverse((mesh) => {
        if (!mesh.isMesh || !mesh.material || !mesh.geometry) return;
        const materials = elementForMesh(mesh, model, elements)?.materials ?? [];
        const sourceMaterials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
        const families = sourceMaterials.map((material) => familyForMaterial(material, materials));
        const needsTexture = withTextures && sourceMaterials.some((material, index) =>
            material.isMeshStandardMaterial && SURFACES[families[index]]?.tile
            && !AUTHORED_MAPS.some((key) => material[key]),
        );
        if (families.every((family) => !family)) counts.unclassifiedMeshes += 1;
        const originalMaterial = mesh.material;
        const originalGeometry = mesh.geometry;
        let useTexture = Boolean(mesh.geometry.getAttribute('uv'));
        if (needsTexture && !useTexture && !mesh.isSkinnedMesh && !mesh.isInstancedMesh) {
            let cache = geometryCache.get(originalGeometry);
            if (!cache) { cache = new Map(); geometryCache.set(originalGeometry, cache); }
            const transform = mesh.matrixWorld.elements.join(',');
            if (!cache.has(transform)) {
                const geometry = createBoxUVGeometry(originalGeometry, mesh.matrixWorld, GEOMETRY_BUDGET - counts.geometryBytes);
                cache.set(transform, geometry);
                if (geometry) {
                    generatedGeometries.add(geometry);
                    counts.geometryBytes += geometry.userData.viewerSurfaceBytes;
                }
            }
            const geometry = cache.get(transform);
            if (geometry) { mesh.geometry = geometry; useTexture = true; }
        }
        if (needsTexture && !useTexture) counts.skippedUV += 1;
        const prepared = sourceMaterials.map((material, index) => preparedMaterial(material, families[index], useTexture));
        if (needsTexture && useTexture) counts.texturedMeshes += 1;
        mesh.material = Array.isArray(originalMaterial) ? prepared : prepared[0];
        originals.push({ mesh, material: originalMaterial, geometry: originalGeometry });
    });
    counts.textureSets = textures.size;
    let disposed = false;
    return {
        counts,
        dispose() {
            if (disposed) return;
            disposed = true;
            for (const { mesh, material, geometry } of originals) {
                mesh.material = material;
                mesh.geometry = geometry;
            }
            for (const material of generatedMaterials) material.dispose();
            for (const geometry of generatedGeometries) geometry.dispose();
            for (const familyTextures of textures.values()) {
                for (const texture of Object.values(familyTextures)) texture.dispose();
            }
        },
    };
}
