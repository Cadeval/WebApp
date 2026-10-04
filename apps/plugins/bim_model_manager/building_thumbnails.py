"""Private, read-only previews from actual IFC geometry and declared colours.

The small orthographic PNG is a presentation preview, not an assessment or a
replacement for the 3D viewer. Cached GLBs avoid repeated native tessellation.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
import fcntl
import json
import math
import os
import re
import struct
import tempfile
import time

import ifcopenshell
import numpy as np
from PIL import Image

from apps.plugins.bim_model_manager.ifc_extractor.material_assessment import file_hash
from apps.plugins.bim_model_manager.ifc_viewer import model_glb


THUMBNAIL_VERSION = 'iso-v2'
SIZE = (320, 200)
BACKGROUND = (246, 246, 245)
MAX_GLB_BYTES = 256 * 1024 * 1024
MAX_TRIANGLES = 2_000_000
MAX_NODES = 200_000
GUID = re.compile(r'[0-9A-Za-z_$]{22}\Z')
HIDDEN_TYPES = {'ifcspace', 'ifcopeningelement', 'ifcvirtualelement'}


class ThumbnailError(ValueError):
    """A preview is unavailable; the caller can show its readable fallback."""


@lru_cache(maxsize=128)
def _source_hash(path, fingerprint):
    return file_hash(path)


def source_fingerprint(source):
    source = Path(source).resolve()
    info = source.stat()
    if not source.is_file() or not info.st_size:
        raise ThumbnailError('The IFC source is empty or unavailable.')
    signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    return source, _source_hash(str(source), signature)


@contextmanager
def _generation_lock(cache, source_hash, ready=None):
    """At most two memory-heavy builders, and one builder per source, across workers."""
    handles = []
    deadline = time.monotonic() + 20
    try:
        own = open(cache / f'thumbnail-{source_hash}.lock', 'a+b')
        os.chmod(own.name, 0o600)
        handles.append(own)
        while True:
            try:
                fcntl.flock(own, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if ready is not None and ready():
                    yield
                    return
                if time.monotonic() >= deadline:
                    raise ThumbnailError('This model preview is being prepared. Please try again shortly.')
                time.sleep(0.05)
        if ready is not None and ready():
            yield
            return
        while True:
            acquired = None
            for slot in range(2):
                handle = open(cache / f'thumbnail-builder-{slot}.lock', 'a+b')
                os.chmod(handle.name, 0o600)
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = handle
                    handles.append(handle)
                    break
                except BlockingIOError:
                    handle.close()
            if acquired is not None:
                break
            if time.monotonic() >= deadline:
                raise ThumbnailError('Other model previews are being prepared. Please try again shortly.')
            time.sleep(0.05)
        yield
    finally:
        for handle in reversed(handles):
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()


def _valid_png(path):
    try:
        if not path.is_file() or path.stat().st_size > 1024 * 1024:
            return False
        with Image.open(path) as image:
            if image.format != 'PNG' or image.size != SIZE:
                return False
            image.verify()
        return True
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return False


def _atomic_json(path, data):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, suffix='.json', delete=False) as output:
        temporary = Path(output.name)
        json.dump(data, output, separators=(',', ':'))
    try:
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _building_products(source, cache, digest, building_guid):
    destination = cache / f'building-membership-v1-{digest}.json'
    try:
        memberships = json.loads(destination.read_text())
        if not isinstance(memberships, dict):
            raise ValueError('Invalid membership cache')
        if any(not GUID.fullmatch(key) or not isinstance(value, list) or any(not isinstance(item, str) or not GUID.fullmatch(item) for item in value)
               for key, value in memberships.items()):
            raise ValueError('Invalid membership cache')
    except (OSError, ValueError, TypeError):
        model = ifcopenshell.open(str(source))
        memberships = {}
        for building in model.by_type('IfcBuilding'):
            queue, visited, products = [building], set(), set()
            while queue:
                element = queue.pop()
                if element.id() in visited:
                    continue
                visited.add(element.id())
                if len(visited) > MAX_NODES:
                    raise ThumbnailError('This building is too large for a small preview. Open the 3D viewer instead.')
                identifier = getattr(element, 'GlobalId', None)
                if identifier and GUID.fullmatch(identifier):
                    products.add(identifier)
                for relation, attribute in [('ContainsElements', 'RelatedElements'), ('IsDecomposedBy', 'RelatedObjects'), ('IsNestedBy', 'RelatedObjects')]:
                    for item in getattr(element, relation, ()):
                        queue.extend(getattr(item, attribute, ()) or ())
            memberships[building.GlobalId] = sorted(products)
        _atomic_json(destination, memberships)
    if building_guid not in memberships:
        raise ThumbnailError('The selected IFC building is not part of this model.')
    return set(memberships[building_guid])


def _read_glb(path):
    size = path.stat().st_size
    if size < 28 or size > MAX_GLB_BYTES:
        raise ThumbnailError('The model geometry is unavailable or exceeds the preview size limit.')
    with path.open('rb') as stream:
        header = stream.read(20)
        magic, version, total, json_length, json_type = struct.unpack('<4sIIII', header)
        if magic != b'glTF' or version != 2 or total != size or json_type != 0x4E4F534A or json_length > 32 * 1024 * 1024:
            raise ThumbnailError('The cached 3D geometry could not be read.')
        document = json.loads(stream.read(json_length))
        binary_length, binary_type = struct.unpack('<II', stream.read(8))
        start = stream.tell()
        if binary_type != 0x004E4942 or start + binary_length > size:
            raise ThumbnailError('The cached 3D geometry could not be read.')
    if len(document.get('nodes', ())) > MAX_NODES:
        raise ThumbnailError('This model has too many parts for a small preview.')
    binary = np.memmap(path, mode='r', dtype=np.uint8, offset=start, shape=(binary_length,))
    return document, binary


def _corrupt_cached_glb(path):
    """Detect incomplete native-cache files without confusing size/encoding policy with corruption."""
    if not path.exists():
        return False
    try:
        size = path.stat().st_size
        if size < 28:
            return True
        with path.open('rb') as stream:
            header = stream.read(20)
            magic, version, total, length, kind = struct.unpack('<4sIIII', header)
            if magic != b'glTF' or version != 2 or total != size or kind != 0x4E4F534A or length + 28 > size:
                return True
            # A valid large JSON chunk is a preview policy limit, not a reason
            # to perform expensive tessellation repeatedly.
            if length <= 32 * 1024 * 1024:
                document = json.loads(stream.read(length))
                if not isinstance(document, dict) or document.get('asset', {}).get('version') != '2.0':
                    return True
            else:
                stream.seek(length, 1)
            binary_length, binary_type = struct.unpack('<II', stream.read(8))
            return binary_type != 0x004E4942 or stream.tell() + binary_length != size
    except (OSError, ValueError, AttributeError, struct.error):
        return True


def _accessor(document, binary, index, components):
    accessor = document['accessors'][index]
    if 'sparse' in accessor or accessor.get('normalized'):
        raise ThumbnailError('This geometry encoding is not supported by the small preview.')
    expected_type = {1: 'SCALAR', 3: 'VEC3'}[components]
    if accessor.get('type') != expected_type:
        raise ThumbnailError('The model geometry has an invalid coordinate layout.')
    dtype = {5121: '<u1', 5123: '<u2', 5125: '<u4', 5126: '<f4'}.get(accessor.get('componentType'))
    if dtype is None:
        raise ThumbnailError('This geometry encoding is not supported by the small preview.')
    view = document['bufferViews'][accessor['bufferView']]
    if view.get('buffer', 0) != 0:
        raise ThumbnailError('External geometry buffers are not supported in small previews.')
    item_size = np.dtype(dtype).itemsize
    stride = view.get('byteStride', item_size * components)
    count = accessor['count']
    offset = view.get('byteOffset', 0) + accessor.get('byteOffset', 0)
    end = offset + max(count - 1, 0) * stride + item_size * components
    view_end = view.get('byteOffset', 0) + view['byteLength']
    if not isinstance(count, int) or count < 0 or count > 8_000_000 or stride < item_size * components or end > view_end or end > len(binary):
        raise ThumbnailError('The cached model contains invalid geometry data.')
    return np.ndarray((count, components), dtype=dtype, buffer=binary, offset=offset, strides=(stride, item_size))


def _node_matrix(node):
    if 'matrix' in node:
        matrix = np.asarray(node['matrix'], dtype=np.float64).reshape((4, 4), order='F')
    else:
        x, y, z, w = node.get('rotation', (0, 0, 0, 1))
        matrix = np.array([[1 - 2*y*y - 2*z*z, 2*x*y - 2*z*w, 2*x*z + 2*y*w, 0],
                           [2*x*y + 2*z*w, 1 - 2*x*x - 2*z*z, 2*y*z - 2*x*w, 0],
                           [2*x*z - 2*y*w, 2*y*z + 2*x*w, 1 - 2*x*x - 2*y*y, 0],
                           [0, 0, 0, 1]], dtype=np.float64)
        matrix[:3, :3] *= np.asarray(node.get('scale', (1, 1, 1)))[None, :]
        matrix[:3, 3] = node.get('translation', (0, 0, 0))
    if not np.isfinite(matrix).all():
        raise ThumbnailError('The model contains unreadable placement coordinates.')
    return matrix


def _triangles(document, binary, product_guids):
    nodes, meshes, materials = document.get('nodes', []), document.get('meshes', []), document.get('materials', [])
    scene = document.get('scenes', [{}])[document.get('scene', 0)]
    roots = scene.get('nodes', [])
    queue = [(index, np.eye(4), None, set()) for index in reversed(roots)]
    batches, colours, double_sided, count, visited = [], [], [], 0, 0
    while queue:
        index, parent, owner, ancestors = queue.pop()
        visited += 1
        if visited > MAX_NODES or len(ancestors) > 64 or index in ancestors or not isinstance(index, int) or index < 0 or index >= len(nodes):
            raise ThumbnailError('The model contains an invalid placement hierarchy.')
        node = nodes[index]
        extra = node.get('extras', {})
        owner = extra if extra.get('GlobalId') else owner
        matrix = parent @ _node_matrix(node)
        for child in reversed(node.get('children', [])):
            queue.append((child, matrix, owner, ancestors | {index}))
        if 'mesh' not in node or str((owner or {}).get('ifcType', '')).lower() in HIDDEN_TYPES:
            continue
        if product_guids is not None and (owner or {}).get('GlobalId') not in product_guids:
            continue
        for primitive in meshes[node['mesh']].get('primitives', []):
            if primitive.get('mode', 4) != 4:
                continue
            vertices = _accessor(document, binary, primitive['attributes']['POSITION'], 3)
            if not np.isfinite(vertices).all():
                raise ThumbnailError('The model contains unreadable surface coordinates.')
            indices = _accessor(document, binary, primitive['indices'], 1).reshape(-1) if 'indices' in primitive else np.arange(len(vertices))
            if len(indices) % 3 or (len(indices) and indices.max() >= len(vertices)):
                raise ThumbnailError('The cached model contains invalid triangle data.')
            count += len(indices) // 3
            if count > MAX_TRIANGLES:
                raise ThumbnailError('This model is too detailed for a small preview. Open the 3D viewer instead.')
            points = (np.asarray(vertices, dtype=np.float64) @ matrix[:3, :3].T + matrix[:3, 3])[indices.reshape((-1, 3))]
            material = materials[primitive['material']] if 'material' in primitive else {}
            colour = material.get('pbrMetallicRoughness', {}).get('baseColorFactor', [.7, .7, .7, 1])
            if len(colour) != 4 or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in colour):
                raise ThumbnailError('The cached model contains unreadable appearance data.')
            colour = np.clip(colour, 0, 1)
            if material.get('alphaMode', 'OPAQUE') == 'OPAQUE':
                colour[3] = 1
            batches.append(points)
            colours.append(np.broadcast_to(colour, (len(points), 4)))
            double_sided.append(np.full(len(points), material.get('doubleSided', False), dtype=bool))
    if not batches:
        raise ThumbnailError('No renderable building surfaces are available for this preview.')
    return np.concatenate(batches), np.concatenate(colours), np.concatenate(double_sided)


def _rasterize(projected, depths, rgba, visible, width, height):
    """Bounded tiled depth buffer avoids painter artifacts on long roof faces.

    At most 256 triangles × 256 pixels are evaluated together. Transparent
    surfaces use the nearest layer in front of opaque geometry, an intentional
    small-preview approximation which keeps hidden glass behind walls hidden.
    """
    tile_size = 16
    columns, rows = math.ceil(width / tile_size), math.ceil(height / tile_size)
    active = np.flatnonzero(visible)
    projected = projected.astype(np.float32)
    depths = depths.astype(np.float32)
    low = np.floor(projected[active].min(axis=1) / tile_size).astype(np.int32)
    high = np.floor(projected[active].max(axis=1) / tile_size).astype(np.int32)
    low = np.clip(low, [0, 0], [columns - 1, rows - 1])
    high = np.clip(high, [0, 0], [columns - 1, rows - 1])
    tile_width = high[:, 0] - low[:, 0] + 1
    counts = tile_width * (high[:, 1] - low[:, 1] + 1)
    if counts.sum() > 8_000_000:
        raise ThumbnailError('This model is too complex for a small preview. Open the 3D viewer instead.')
    starts = np.cumsum(counts) - counts
    steps = np.arange(counts.sum()) - np.repeat(starts, counts)
    repeated_width = np.repeat(tile_width, counts)
    keys = (np.repeat(low[:, 1], counts) + steps // repeated_width) * columns + np.repeat(low[:, 0], counts) + steps % repeated_width
    members = np.repeat(active, counts)
    order = np.argsort(keys, kind='stable')
    keys, members = keys[order], members[order]
    boundaries = np.r_[0, np.flatnonzero(np.diff(keys)) + 1, len(keys)]
    pixels = np.full((height, width, 3), BACKGROUND, dtype=np.uint8)
    for start, stop in zip(boundaries[:-1], boundaries[1:]):
        key = int(keys[start]); x, y = key % columns * tile_size, key // columns * tile_size
        w, h = min(tile_size, width - x), min(tile_size, height - y)
        yy, xx = np.mgrid[y:y+h, x:x+w].astype(np.float32)
        px, py = xx.reshape(-1) + .5, yy.reshape(-1) + .5
        best_depth = np.full(len(px), -np.inf, dtype=np.float32)
        colour = np.full((len(px), 3), BACKGROUND, dtype=np.uint8)
        candidates = members[start:stop]

        def layer(indices, depth_buffer, colour_buffer):
            for offset in range(0, len(indices), 256):
                selected = indices[offset:offset+256]
                a, b, c = np.moveaxis(projected[selected], 1, 0)
                denominator = (b[:, 1]-c[:, 1]) * (a[:, 0]-c[:, 0]) + (c[:, 0]-b[:, 0]) * (a[:, 1]-c[:, 1])
                nondegenerate = np.abs(denominator) > 1e-6
                if not nondegenerate.any():
                    continue
                selected, a, b, c, denominator = selected[nondegenerate], a[nondegenerate], b[nondegenerate], c[nondegenerate], denominator[nondegenerate]
                first = ((b[:, 1]-c[:, 1])[:, None] * (px-c[:, 0, None]) + (c[:, 0]-b[:, 0])[:, None] * (py-c[:, 1, None])) / denominator[:, None]
                second = ((c[:, 1]-a[:, 1])[:, None] * (px-c[:, 0, None]) + (a[:, 0]-c[:, 0])[:, None] * (py-c[:, 1, None])) / denominator[:, None]
                third = 1 - first - second
                inside = (first >= -1e-6) & (second >= -1e-6) & (third >= -1e-6)
                depth = first * depths[selected, 0, None] + second * depths[selected, 1, None] + third * depths[selected, 2, None]
                depth[~inside] = -np.inf
                closest = depth.argmax(axis=0)
                candidate_depth = depth[closest, np.arange(len(px))]
                wins = candidate_depth > depth_buffer + 1e-5
                depth_buffer[wins] = candidate_depth[wins]
                colour_buffer[wins] = rgba[selected[closest[wins]], :colour_buffer.shape[1]]

        layer(candidates[rgba[candidates, 3] == 255], best_depth, colour)
        transparent_colour = np.zeros((len(px), 4), dtype=np.uint8)
        layer(candidates[(rgba[candidates, 3] > 0) & (rgba[candidates, 3] < 255)], best_depth.copy(), transparent_colour)
        alpha = transparent_colour[:, 3:4].astype(np.float32) / 255
        result = np.rint(transparent_colour[:, :3] * alpha + colour * (1 - alpha)).astype(np.uint8)
        pixels[y:y+h, x:x+w] = result.reshape(h, w, 3)
    return Image.fromarray(pixels)


def render_glb_thumbnail(glb_path, output_png, *, product_guids=None):
    """Render trusted cached GLB surfaces to an atomic small PNG; no external files/URLs."""
    temporary = None
    try:
        glb_path, output_png = Path(glb_path), Path(output_png)
        document, binary = _read_glb(glb_path)
        triangles, colours, double_sided = _triangles(document, binary, product_guids)
        low, high = triangles.min(axis=(0, 1)), triangles.max(axis=(0, 1))
        center = (low + high) / 2
        triangles -= center
        direction = np.asarray([1, .85, 1.6]); direction /= np.linalg.norm(direction)
        right = np.cross([0, 1, 0], direction); right /= np.linalg.norm(right)
        up = np.cross(direction, right)
        projected = np.stack((triangles @ right, -(triangles @ up)), axis=2)
        lower, upper = projected.min(axis=(0, 1)), projected.max(axis=(0, 1))
        span = upper - lower
        if not np.isfinite(span).all() or max(span) < 1e-9:
            raise ThumbnailError('The building has no visible dimensions for a preview.')
        supersample = 2
        width, height = SIZE[0] * supersample, SIZE[1] * supersample
        scale = min((width - 40 * supersample) / max(span[0], 1e-9), (height - 32 * supersample) / max(span[1], 1e-9))
        projected = (projected - (lower + upper) / 2) * scale + np.asarray([width / 2, height / 2])
        a, b = projected[:, 1] - projected[:, 0], projected[:, 2] - projected[:, 0]
        area = np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]) / 2
        extent = projected.max(axis=1) - projected.min(axis=1)
        # Only discard faces too small to contribute a pixel. Long thin faces
        # remain, including rails and large surfaces seen almost edge-on.
        visible = ((area > .06 * supersample**2) | (extent.max(axis=1) > supersample)) & (area > 1e-8)
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        lengths = np.linalg.norm(normals, axis=1)
        visible &= lengths > 1e-12
        normals /= np.maximum(lengths[:, None], 1e-12)
        toward = normals @ direction
        visible &= double_sided | (toward > 0)
        normals[double_sided & (toward < 0)] *= -1
        sun = np.asarray([-.4, 1, .7]); sun /= np.linalg.norm(sun)
        shade = .48 + .52 * np.clip(normals @ sun, 0, 1)
        linear = np.clip(colours[:, :3] * shade[:, None], 0, 1)
        rgb = np.where(linear <= .0031308, linear * 12.92, 1.055 * linear**(1 / 2.4) - .055)
        rgba = np.concatenate((rgb, colours[:, 3:4]), axis=1)
        rgba = np.clip(np.rint(rgba * 255), 0, 255).astype(np.uint8)
        if not visible.any():
            raise ThumbnailError('The building has no visible surfaces for a preview.')
        image = _rasterize(projected, triangles @ direction, rgba, visible, width, height)
        image = image.resize(SIZE, Image.Resampling.LANCZOS)
        output_png.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=output_png.parent, suffix='.png', delete=False) as output:
            temporary = Path(output.name)
            image.save(output, format='PNG', optimize=True)
        os.chmod(temporary, 0o600)
        os.replace(temporary, output_png)
        return output_png
    except ThumbnailError:
        raise
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError, OverflowError, struct.error) as error:
        raise ThumbnailError('The model geometry could not be read for a preview.') from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def model_thumbnail(source_path, cache_directory, *, building_guid='', geometry_cache_directory=None):
    """Return private deterministic PNG; caller must authorize the original upload first."""
    try:
        if not isinstance(building_guid, str) or (building_guid and not GUID.fullmatch(building_guid)):
            raise ThumbnailError('The selected IFC building identifier is invalid.')
        source, digest = source_fingerprint(source_path)
        cache = Path(cache_directory)
        cache.mkdir(parents=True, exist_ok=True)
        os.chmod(cache, 0o700)
        destination = cache / f'thumbnail-{THUMBNAIL_VERSION}-{digest}-{building_guid or "model"}.png'
        if _valid_png(destination):
            return destination
        with _generation_lock(cache, digest, ready=lambda: _valid_png(destination)):
            if _valid_png(destination):
                return destination
            products = _building_products(source, cache, digest, building_guid) if building_guid else None
            geometry_cache = Path(geometry_cache_directory) if geometry_cache_directory is not None else cache / 'geometry'
            expected_glb = geometry_cache / f'identity-v2-{digest}.glb'
            if _corrupt_cached_glb(expected_glb):
                expected_glb.unlink()
            glb = model_glb(source, geometry_cache)
            return render_glb_thumbnail(glb, destination, product_guids=products)
    except ThumbnailError:
        raise
    except (OSError, ValueError, RuntimeError, ifcopenshell.Error) as error:
        raise ThumbnailError('The IFC source could not be read for a building preview.') from error
