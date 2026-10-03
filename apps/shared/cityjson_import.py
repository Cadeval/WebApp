"""Bounded CityJSON import through the official IfcCityJSON converter.

The original document is retained by the caller. Conversion uses a private
copy, one explicitly selected LoD and a worker process; no URL is fetched.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import warnings
from collections import Counter, deque
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace


class CityJSONImportError(ValueError):
    """An actionable import failure suitable for a form error."""


@dataclass(frozen=True)
class CityJSONLimits:
    max_bytes: int = 50 * 1024 * 1024
    max_output_bytes: int = 500 * 1024 * 1024
    max_city_objects: int = 50_000
    max_vertices: int = 1_000_000
    max_faces: int = 250_000
    max_boundary_references: int = 2_000_000
    max_depth: int = 32
    max_warnings: int = 20


OBJECT_TYPES = frozenset(('Building', 'BuildingPart', 'BuildingInstallation',
    'BuildingConstructiveElement', 'BuildingFurniture', 'BuildingStorey', 'BuildingRoom',
    'BuildingUnit', 'Road', 'Railway', 'TransportationSquare', 'TINRelief', 'WaterBody',
    'LandUse', 'PlantCover', 'SolitaryVegetationObject', 'CityFurniture', 'OtherConstruction',
    '+GenericCityObject', 'Bridge', 'BridgePart', 'BridgeInstallation', 'BridgeConstructiveElement',
    'BridgeRoom', 'BridgeFurniture', 'Tunnel', 'TunnelPart', 'TunnelInstallation',
    'TunnelConstructiveElement', 'TunnelHollowSpace', 'TunnelFurniture', 'CityObjectGroup'))
GEOMETRY_TYPES = frozenset(('MultiSurface', 'CompositeSurface', 'Solid', 'MultiSolid', 'CompositeSolid'))
SURFACE_TYPES = frozenset(('GroundSurface', 'RoofSurface', 'WallSurface', 'ClosureSurface',
    'OuterCeilingSurface', 'OuterFloorSurface', 'Window', 'Door', 'InteriorWallSurface',
    'CeilingSurface', 'FloorSurface', 'WaterSurface', 'WaterGroundSurface', 'WaterClosureSurface',
    'TrafficArea', 'AuxiliaryTrafficArea', 'TransportationMarking', 'TransportationHole'))
EPSG_URI = re.compile(r'^https?://www\.opengis\.net/def/crs/EPSG/[^/]+/(\d+)$')
LOD = re.compile(r'^\d{1,2}(?:\.\d{1,8})?$')


def _fail(message):
    raise CityJSONImportError(message)


def _finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _lod(value):
    text = str(value)
    if not LOD.fullmatch(text) or not 0 <= Decimal(text) <= 4:
        _fail('Each geometry needs a numeric LoD between 0 and 4, such as 1 or 2.2.')
    return format(Decimal(text).normalize(), 'f')


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail('The CityJSON document contains duplicate keys. Make object IDs and attribute names unique.')
        result[key] = value
    return result


def _read(source, limits):
    try:
        with Path(source).open('rb') as file:
            raw = file.read(limits.max_bytes + 1)
        if len(raw) > limits.max_bytes:
            _fail(f'The CityJSON file exceeds the {limits.max_bytes // (1024 * 1024)} MB import limit.')
        data = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_pairs,
            parse_constant=lambda value: _fail('Coordinates and attributes must use finite JSON numbers.'))
    except CityJSONImportError:
        raise
    except json.JSONDecodeError as error:
        _fail(f'The file is not valid JSON (line {error.lineno}, column {error.colno}).')
    except UnicodeError:
        _fail('Save the CityJSON document as UTF-8 text.')
    except RecursionError:
        _fail('The CityJSON document is nested too deeply.')
    except ValueError:
        _fail('The CityJSON document contains an unusable number. Use finite coordinates within numeric limits.')
    except OSError:
        _fail('The CityJSON source file could not be read.')
    stack = [(data, 0)]
    while stack:
        value, depth = stack.pop()
        if depth > limits.max_depth:
            _fail(f'The CityJSON document exceeds the {limits.max_depth}-level nesting limit.')
        if isinstance(value, dict):
            stack.extend((v, depth + 1) for v in value.values())
        elif isinstance(value, list):
            stack.extend((v, depth + 1) for v in value)
        elif isinstance(value, float) and not math.isfinite(value):
            _fail('Coordinates and attributes must use finite JSON numbers.')
    return data, hashlib.sha256(raw).hexdigest()


def _hierarchy(objects):
    edges = set()
    for identifier, obj in objects.items():
        for field, reverse in (('parents', True), ('children', False)):
            values = obj.get(field, [])
            if not isinstance(values, list) or any(not isinstance(v, str) or v not in objects for v in values):
                _fail('Every parent and child reference must point to an existing CityObject.')
            edges.update((v, identifier) if reverse else (identifier, v) for v in values)
    parents = {key: set() for key in objects}; children = {key: set() for key in objects}
    for parent, child in edges:
        parents[child].add(parent); children[parent].add(child)
    if any(len(v) > 1 for v in parents.values()):
        _fail('A CityObject has multiple parents. Choose one hierarchy before importing to IFC.')
    degree = {key: len(value) for key, value in parents.items()}
    pending = deque(key for key, value in degree.items() if not value); visited = 0
    while pending:
        key = pending.popleft(); visited += 1
        for child in children[key]:
            degree[child] -= 1
            if degree[child] == 0:
                pending.append(child)
    if visited != len(objects):
        _fail('The CityObject hierarchy contains a cycle. Repair its parent and child references.')
    return parents, children


def _crs(data):
    metadata = data.get('metadata', {})
    if not isinstance(metadata, dict):
        _fail('CityJSON metadata must be an object.')
    reference = metadata.get('referenceSystem')
    if reference is None:
        return None, None
    match = EPSG_URI.fullmatch(reference) if isinstance(reference, str) else None
    if not match:
        _fail('Use an EPSG referenceSystem URL such as https://www.opengis.net/def/crs/EPSG/0/32633.')
    try:
        from pyproj import CRS
        crs = CRS.from_epsg(int(match.group(1)))
    except (ImportError, ValueError, RuntimeError):
        _fail('The declared EPSG reference system is unavailable. Choose a known projected metre CRS.')
    if not crs.is_projected or len(crs.axis_info) < 2 or any(
        not math.isclose(axis.unit_conversion_factor, 1.0, rel_tol=1e-9) for axis in crs.axis_info):
        _fail('IfcCityJSON assumes metre coordinates. Reproject geographic or non-metre data to a projected metre CRS first.')
    return int(match.group(1)), crs


def _face_paths(geometry):
    boundaries = geometry.get('boundaries')
    if not isinstance(boundaries, list) or not boundaries:
        _fail('Selected geometry needs non-empty boundaries.')
    kind = geometry['type']
    if kind in ('MultiSurface', 'CompositeSurface'):
        return [(i,) for i in range(len(boundaries))]
    if kind == 'Solid':
        if len(boundaries) != 1:
            _fail('Solid interior shells are not supported. Use one exterior shell or remove the void before importing.')
        if not isinstance(boundaries[0], list):
            _fail('Solid boundaries must contain a shell of polygon faces.')
        return [(0, i) for i in range(len(boundaries[0]))]
    paths = []
    for solid_index, solid in enumerate(boundaries):
        if not isinstance(solid, list) or len(solid) != 1:
            _fail('MultiSolid interior shells are not supported. Each solid needs one exterior shell.')
        if not isinstance(solid[0], list):
            _fail('MultiSolid boundaries must contain shells of polygon faces.')
        paths.extend((solid_index, 0, i) for i in range(len(solid[0])))
    return paths


def _at(value, path):
    for index in path:
        value = value[index]
    return value


def _validate_geometry(geometry, vertices, budgets, limits):
    kind = geometry.get('type')
    if kind == 'GeometryInstance':
        _fail('GeometryInstance is not supported. Expand geometry templates into explicit surfaces before importing.')
    if kind not in GEOMETRY_TYPES:
        _fail('Choose a LoD containing polygon surfaces or solids. Point, line and custom geometry cannot be imported into this viewer.')
    paths = _face_paths(geometry)
    if not paths:
        _fail('Selected geometry has no polygon faces. Choose another LoD or repair its boundaries.')
    budgets['faces'] += len(paths)
    if budgets['faces'] > limits.max_faces:
        _fail(f'The selected LoD exceeds the {limits.max_faces:,}-face import limit.')
    for path in paths:
        face = _at(geometry['boundaries'], path)
        if not isinstance(face, list) or not face:
            _fail('Every polygon face needs an exterior ring.')
        for ring in face:
            if not isinstance(ring, list) or len(ring) < 3 or any(
                not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(vertices) for i in ring):
                _fail('Polygon rings need at least three valid vertex indices. Repair missing or out-of-range references.')
            budgets['references'] += len(ring)
            if budgets['references'] > limits.max_boundary_references:
                _fail('The selected LoD exceeds the boundary-reference import limit.')
            if len({tuple(vertices[i]) for i in ring}) < 3:
                _fail('A polygon ring has fewer than three distinct vertices. Repair the degenerate face.')
            # A Newell normal detects collinear/zero-area rings without fixing
            # winding or flattening sloped source surfaces.
            coordinates = [vertices[i] for i in ring]
            normal = [sum((a[(axis + 1) % 3] - b[(axis + 1) % 3]) *
                          (a[(axis + 2) % 3] + b[(axis + 2) % 3])
                          for a, b in zip(coordinates, coordinates[1:] + coordinates[:1]))
                      for axis in range(3)]
            if not all(math.isfinite(v) for v in normal) or not any(normal):
                _fail('A polygon ring has zero area or unusable coordinates. Repair the degenerate face.')
    semantics = geometry.get('semantics')
    if semantics is not None:
        if not isinstance(semantics, dict) or not isinstance(semantics.get('surfaces'), list):
            _fail('Geometry semantics must define a surfaces array and matching face values.')
        if any(not isinstance(surface, dict) or surface.get('type') not in SURFACE_TYPES
               for surface in semantics['surfaces']):
            _fail('A semantic surface type is unsupported by IfcCityJSON. Use standard CityJSON surface types.')
        values = semantics.get('values')
        try:
            def shape_matches(boundaries, values, depth):
                return (isinstance(values, list) and len(values) == len(boundaries) and
                    (depth == 1 or all(shape_matches(b, v, depth - 1) for b, v in zip(boundaries, values))))
            if not shape_matches(geometry['boundaries'], values, len(paths[0])):
                raise ValueError
            for path in paths:
                index = _at(values, path)
                if index is not None and (not isinstance(index, int) or isinstance(index, bool)
                    or not 0 <= index < len(semantics['surfaces'])):
                    raise ValueError
        except (IndexError, KeyError, TypeError, ValueError):
            _fail('Semantic values must match the geometry faces and reference existing semantic surfaces.')


def _inspect(data, digest, limits, selected_lod=None):
    if not isinstance(data, dict) or data.get('type') != 'CityJSON':
        _fail('Upload a CityJSON document with type CityJSON; JSON sequences and feature fragments are not supported.')
    if data.get('version') not in ('1.0', '1.1', '2.0'):
        _fail('This importer supports CityJSON versions 1.0, 1.1 and 2.0.')
    objects = data.get('CityObjects'); vertices = data.get('vertices')
    if not isinstance(objects, dict) or not objects or len(objects) > limits.max_city_objects:
        _fail(f'Provide between 1 and {limits.max_city_objects:,} CityObjects.')
    if not isinstance(vertices, list) or not vertices or len(vertices) > limits.max_vertices:
        _fail(f'Provide between 1 and {limits.max_vertices:,} vertices.')
    if any(not isinstance(v, list) or len(v) != 3 or not all(_finite(x) for x in v) for v in vertices):
        _fail('Every vertex must contain three finite numeric coordinates.')
    transform = data.get('transform')
    if transform is not None and (not isinstance(transform, dict)
        or any(not isinstance(transform.get(k), list) or len(transform[k]) != 3
               or not all(_finite(v) for v in transform[k]) for k in ('scale', 'translate'))
        or any(v <= 0 for v in transform['scale'])):
        _fail('The transform needs three positive finite scale values and three finite translation values.')
    if transform and any(not _finite(v[i] * transform['scale'][i] + transform['translate'][i])
                         for v in vertices for i in range(3)):
        _fail('The transform produces coordinates outside numeric limits. Rescale or reproject the source model.')
    lods = set(); blocked = set(); notices = []; candidates = Counter(); budgets = {'faces': 0, 'references': 0}
    for identifier, obj in objects.items():
        if not isinstance(identifier, str) or not identifier or len(identifier) > 512 or not isinstance(obj, dict):
            _fail('CityObject IDs must be non-empty strings of at most 512 characters, each with an object value.')
        if obj.get('type') not in OBJECT_TYPES:
            _fail('A CityObject type is unsupported by IfcCityJSON. Convert extension objects to a supported standard type.')
        if not isinstance(obj.get('attributes', {}), dict):
            _fail('CityObject attributes must be an object.')
        geometries = obj.get('geometry', [])
        if not isinstance(geometries, list):
            _fail('Each CityObject geometry must be an array.')
        for geometry in geometries:
            if not isinstance(geometry, dict):
                _fail('Every geometry entry must be an object.')
            level = _lod(geometry.get('lod')); lods.add(level)
            if geometry.get('type') in GEOMETRY_TYPES:
                candidates[level] += 1
            else:
                blocked.add(level)
                if selected_lod is None and len(notices) < limits.max_warnings:
                    notices.append(f'LoD {level} contains unsupported geometry; choose a supported level or expand its geometry.')
            if selected_lod == level:
                _validate_geometry(geometry, vertices, budgets, limits)
    _hierarchy(objects)
    epsg, crs = _crs(data)
    if not lods:
        _fail('The document has no geometry with a LoD to import.')
    if selected_lod is not None and selected_lod not in lods:
        _fail('The selected LoD is not present in this CityJSON document.')
    if selected_lod is not None and not candidates[selected_lod]:
        _fail('The selected LoD has no supported polygon surface or solid geometry.')
    if not epsg:
        notices.append('No CRS is declared. Coordinates are interpreted as metres; map locations remain unavailable.')
    if data.get('appearance'):
        notices.append('CityJSON appearance colours, materials and textures are retained in the source document; IfcCityJSON does not convert them to IFC material properties.')
    notices.append('CityJSON surfaces do not provide construction layers or verified material quantities. Calculate impacts only after supplying suitable material data.')
    supported = set(candidates) - blocked
    return {'source_sha256': digest, 'version': data['version'],
        'lods': sorted(lods, key=Decimal), 'default_lod': max(supported or lods, key=Decimal),
        'supported_lods': sorted(supported, key=Decimal), 'city_objects': len(objects),
        'vertices': len(vertices), 'selected_faces': budgets['faces'],
        'georeferencing': {'reference_system': data.get('metadata', {}).get('referenceSystem'),
                          'epsg': epsg, 'source_transform': transform},
        'warnings': list(dict.fromkeys(notices))[:limits.max_warnings]}


def inspect_cityjson(source_path, *, limits=CityJSONLimits()):
    data, digest = _read(source_path, limits)
    return _inspect(data, digest, limits)


def convert_cityjson(source_path, destination_path, *, lod, name_attribute=None,
                    timeout_seconds=60, limits=CityJSONLimits()):
    """Publish one IFC atomically after a bounded, isolated conversion."""
    if not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 600:
        _fail('The conversion timeout must be between 0 and 600 seconds.')
    if name_attribute is not None and (not isinstance(name_attribute, str) or len(name_attribute) > 512):
        _fail('Choose a source name attribute of at most 512 characters.')
    selected = _lod(lod)
    data, digest = _read(source_path, limits)
    inspection = _inspect(data, digest, limits, selected)
    destination = Path(destination_path)
    if destination.resolve() == Path(source_path).resolve():
        _fail('Save the converted IFC to a separate path so the original CityJSON is retained.')
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='cadevil-cityjson-', dir=destination.parent) as folder:
            folder = Path(folder); output = folder / 'converted.ifc'; job = folder / 'job.json'
            job.write_text(json.dumps({'source': data, 'inspection': inspection, 'lod': selected,
                'name_attribute': name_attribute, 'limits': asdict(limits)}, ensure_ascii=False, allow_nan=False))
            env = dict(os.environ); env['PROJ_NETWORK'] = 'OFF'
            env['PYTHONPATH'] = os.pathsep.join([str(Path(__file__).resolve().parents[2]), env.get('PYTHONPATH', '')])
            try:
                result = subprocess.run([sys.executable, '-m', 'apps.shared.cityjson_import', '--worker',
                    str(job), str(output), str(math.ceil(timeout_seconds))], stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, timeout=timeout_seconds, env=env, check=False)
            except subprocess.TimeoutExpired:
                _fail('CityJSON conversion exceeded its time limit. Use a smaller model or a less detailed LoD.')
            except OSError:
                _fail('The CityJSON conversion worker could not be started.')
            try:
                report = json.loads(result.stdout)
            except (ValueError, UnicodeError):
                _fail('CityJSON conversion stopped before completing. Reduce the model size or repair its selected geometry.')
            if result.returncode or not report.get('ok'):
                _fail(report.get('message', 'The selected CityJSON geometry could not be converted. Repair it or choose another LoD.')[:500])
            if not output.exists() or not 0 < output.stat().st_size <= limits.max_output_bytes:
                _fail('The converted IFC is empty or exceeds the output size limit.')
            os.replace(output, destination)
            report.pop('ok', None)
            return {**inspection, **report, 'lod': selected, 'output_path': str(destination)}
    except CityJSONImportError:
        raise
    except (OSError, ValueError):
        _fail('The converted IFC could not be saved. Check available storage and try again.')


def _normalise(data, selected):
    normal = copy.deepcopy(data)
    parents, children = _hierarchy(normal['CityObjects'])
    source_transform = normal.get('transform', {'scale': [1, 1, 1], 'translate': [0, 0, 0]})
    epsg, _ = _crs(normal)
    if not epsg:
        normal['vertices'] = [[v[i] * source_transform['scale'][i] + source_transform['translate'][i]
                               for i in range(3)] for v in normal['vertices']]
        normal['transform'] = {'scale': [1, 1, 1], 'translate': [0, 0, 0]}
    elif 'transform' not in normal:
        origin = [min(v[i] for v in normal['vertices']) for i in range(3)]
        normal['vertices'] = [[v[i] - origin[i] for i in range(3)] for v in normal['vertices']]
        normal['transform'] = {'scale': [1, 1, 1], 'translate': origin}
    normal.pop('appearance', None)
    for identifier, obj in normal['CityObjects'].items():
        obj['parents'] = sorted(parents[identifier]); obj['children'] = sorted(children[identifier])
        obj['geometry'] = [g for g in obj.get('geometry', []) if _lod(g['lod']) == selected]
        for geometry in obj['geometry']:
            geometry['lod'] = selected; geometry.pop('texture', None); geometry.pop('material', None)
            for path in _face_paths(geometry):
                for ring in _at(geometry['boundaries'], path):
                    if len(ring) > 3 and ring[0] == ring[-1]:
                        ring.pop()
        obj['attributes'] = {key: (json.dumps(value, ensure_ascii=False, allow_nan=False)
            if value is None or isinstance(value, (dict, list)) else value)
            for key, value in obj.get('attributes', {}).items()}
    return normal


class _BoundedLog(io.StringIO):
    def write(self, text):
        super().write(text[:max(0, 8192 - self.tell())])
        return len(text)


def _convert_worker(job, output, cpu_seconds):
    import ifcopenshell
    import ifcopenshell.api.pset
    from cjio import cityjson
    from ifccityjson.cityjson2ifc import Cityjson2ifc
    from ifccityjson.cityjson2ifc.geometry import GeometryIO
    from pyproj import network
    network.set_network_enabled(False)
    limits = CityJSONLimits(**job['limits'])
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds + 1, cpu_seconds + 2))
        resource.setrlimit(resource.RLIMIT_FSIZE, (limits.max_output_bytes, limits.max_output_bytes))
    except (ImportError, OSError, ValueError):
        pass
    source = job['source']; selected = job['lod']; normal = _normalise(source, selected)
    source_key = '__cadevil_cityobject_id'
    while any(source_key in obj.get('attributes', {}) for obj in normal['CityObjects'].values()):
        source_key += '_'
    for identifier, obj in normal['CityObjects'].items():
        obj['attributes'][source_key] = identifier
        if job.get('name_attribute') and job['name_attribute'] in obj['attributes']:
            value = obj['attributes'][job['name_attribute']]
            obj['attributes'][job['name_attribute']] = str(value)
    city_model = cityjson.CityJSON(j=normal)
    city_model.load_from_j(transform=False)
    # cjio's flag describes transformed API vertices, but IfcCityJSON 0.8.5
    # uses it to detect the source transform. Keep vertices raw and explicitly
    # enable its scale/MapConversion handling; tests reconstruct world points.
    city_model.is_transformed = True

    class SourceGeometry(GeometryIO):
        def create_IFC_surface(self, model, geometry, surface_id=None):
            if surface_id is None:
                return super().create_IFC_surface(model, geometry)
            paths = geometry.surfaces[surface_id].get('surface_idx')
            if not paths:
                return None
            faces = [_at(geometry.boundaries, path) for path in paths]
            return super().create_IFC_surface(model, SimpleNamespace(boundaries=faces))

    class SourceConverter(Cityjson2ifc):
        def __init__(self):
            super().__init__(); self.geometry = SourceGeometry(); self.source_guids = {}

        def create_property_set(self, attributes, entity):
            identifier = attributes[source_key]
            super().create_property_set({key: value for key, value in attributes.items() if key != source_key}, entity)
            self.source_guids[identifier] = entity.GlobalId
            provenance = ifcopenshell.api.pset.add_pset(self.IFC_model, product=entity, name='CadevilCityJSON')
            ifcopenshell.api.pset.edit_pset(self.IFC_model, pset=provenance, properties={
                'SourceCityObjectId': identifier, 'SourceSHA256': job['inspection']['source_sha256'],
                'SourceVersion': source['version'], 'SelectedLoD': selected,
                'SourceAttributesJSON': json.dumps(source['CityObjects'][identifier].get('attributes', {}), ensure_ascii=False),
                'ReferenceSystem': source.get('metadata', {}).get('referenceSystem', ''),
                'SourceTransformJSON': json.dumps(source.get('transform'))})

        def create_IFC_shape_representation(self, item, representation_type, lod):
            # All supported polygon paths produce IfcShellBasedSurfaceModel.
            # The upstream lowercase 'brep' violates IFC representation rules.
            if representation_type == 'brep':
                representation_type = 'SurfaceModel'
            return super().create_IFC_shape_representation(item, representation_type, lod)

        def create_IFC_semantic_surface_children(self, geometry, lod):
            children = super().create_IFC_semantic_surface_children(geometry, lod)
            assigned = {tuple(path) for surface in geometry.surfaces.values()
                        for path in surface.get('surface_idx') or ()}
            shape = {'type': geometry.type, 'boundaries': geometry.boundaries}
            missing = [path for path in _face_paths(shape) if path not in assigned]
            if missing:
                item = self.geometry.create_IFC_surface(self.IFC_model,
                    SimpleNamespace(boundaries=[_at(geometry.boundaries, path) for path in missing]))
                representation = self.create_IFC_shape_representation(item, 'brep', lod)
                children.append(self.IFC_model.create_entity('IfcBuildingElementProxy',
                    GlobalId=ifcopenshell.guid.new(), Name='Unclassified CityJSON surfaces',
                    Representation=self.IFC_model.create_entity('IfcProductDefinitionShape', Representations=[representation])))
            return children

    converter = SourceConverter()
    converter.configuration(file_destination=str(output), name_attribute=job.get('name_attribute'),
        split=False, lod=selected, name_project='CityJSON import', name_site='CityJSON site',
        name_person_given='Cadevil')
    log = _BoundedLog()
    with contextlib.redirect_stdout(log):
        converter.convert(city_model)
    model = converter.IFC_model
    # Upstream emits represented products without placements, contrary to
    # IfcProduct.PlacementForShapeRepresentation. Its vertices are already in
    # a shared local frame; global identity placements preserve that frame.
    identity = model.create_entity('IfcAxis2Placement3D',
        Location=model.create_entity('IfcCartesianPoint', Coordinates=(0., 0., 0.)))
    for product in model.by_type('IfcProduct'):
        if product.Representation and product.ObjectPlacement is None:
            product.ObjectPlacement = model.create_entity('IfcLocalPlacement', RelativePlacement=identity)
    # Semantic children of roads/bridges/etc. cannot use spatial containment.
    # Preserve the upstream grouping as a valid object decomposition instead.
    for relation in model.by_type('IfcRelContainedInSpatialStructure'):
        if not relation.RelatingStructure.is_a('IfcSpatialStructureElement'):
            model.create_entity('IfcRelAggregates', GlobalId=ifcopenshell.guid.new(),
                RelatingObject=relation.RelatingStructure, RelatedObjects=relation.RelatedElements)
            model.remove(relation)
    geometry_products = sum(bool(product.Representation) for product in model.by_type('IfcProduct'))
    if not geometry_products:
        _fail('The selected LoD produced no IFC geometry. Choose another level or repair the source boundaries.')
    buildings = _building_records(source, selected, converter.source_guids, model)
    model.write(str(output))
    notices = list(job['inspection']['warnings'])
    notices.extend(line[:350] for line in log.getvalue().splitlines() if line.startswith('Warning:'))
    return {'ok': True, 'output_sha256': hashlib.sha256(Path(output).read_bytes()).hexdigest(),
            'converted_objects': len(converter.source_guids), 'geometry_products': geometry_products,
            'buildings': buildings, 'warnings': list(dict.fromkeys(notices))[:limits.max_warnings]}


def _building_records(source, selected, mapping, model):
    from pyproj import Transformer
    from pyproj.exceptions import ProjError
    epsg, crs = _crs(source)
    transform = source.get('transform', {'scale': [1, 1, 1], 'translate': [0, 0, 0]})
    parents, descendants = _hierarchy(source['CityObjects'])
    # Combine descendant bounds once, from leaves upward. Walking the entire
    # subtree separately for every BuildingPart becomes quadratic in a deeply
    # nested hierarchy, despite the upload's object/reference limits.
    bounds = {}
    for identifier, obj in source['CityObjects'].items():
        used = set()
        for geometry in obj.get('geometry', []):
            if _lod(geometry['lod']) != selected:
                continue
            for path in _face_paths(geometry):
                for ring in _at(geometry['boundaries'], path):
                    used.update(ring)
        if used:
            coordinates = [[source['vertices'][index][axis] * transform['scale'][axis] + transform['translate'][axis]
                            for axis in range(3)] for index in used]
            bounds[identifier] = tuple((min(v[axis] for v in coordinates), max(v[axis] for v in coordinates)) for axis in range(3))
        else:
            bounds[identifier] = None
    remaining = {key: len(children) for key, children in descendants.items()}
    pending = deque(key for key, count in remaining.items() if not count)
    while pending:
        child = pending.popleft()
        for parent in parents[child]:
            if bounds[child]:
                bounds[parent] = (tuple((min(a[0], b[0]), max(a[1], b[1]))
                    for a, b in zip(bounds[parent], bounds[child])) if bounds[parent] else bounds[child])
            remaining[parent] -= 1
            if not remaining[parent]:
                pending.append(parent)
    transformer = None
    if crs:
        try:
            transformer = Transformer.from_crs(crs, 4326, always_xy=True, allow_ballpark=False, only_best=True)
        except ProjError:
            pass
    result = []
    for identifier, guid in mapping.items():
        entity = model.by_guid(guid)
        if not entity.is_a('IfcBuilding'):
            continue
        row = {'guid': guid, 'source_id': identifier, 'name': entity.Name or identifier,
               'latitude': None, 'longitude': None, 'source': 'cityjson_geometry',
               'crs': f'EPSG:{epsg}' if epsg else None, 'status': 'unavailable',
               'message': 'No declared CRS or selected building geometry is available for its map location.'}
        if crs and not transformer:
            row['message'] = 'A precise offline transformation from the declared CRS is unavailable; its map location is unavailable.'
        if bounds[identifier] and transformer:
            centre = [(minimum + maximum) / 2 for minimum, maximum in bounds[identifier]]
            try:
                longitude, latitude = transformer.transform(centre[0], centre[1], errcheck=True)
                if _finite(latitude) and _finite(longitude) and -90 <= latitude <= 90 and -180 <= longitude <= 180:
                    row.update(latitude=latitude, longitude=longitude, status='located', representative_point=centre,
                               message='Location is the centre of selected CityJSON geometry bounds, including building parts.')
                else:
                    row['message'] = 'The selected geometry bounds are outside the declared CRS; its map location is unavailable.'
            except ProjError:
                row['message'] = 'The selected geometry bounds cannot be transformed from the declared CRS; its map location is unavailable.'
        result.append(row)
    return result


def _main(argv):
    if len(argv) != 5 or argv[1] != '--worker':
        return 2
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', DeprecationWarning)
            report = _convert_worker(json.loads(Path(argv[2]).read_text()), Path(argv[3]), int(argv[4]))
        print(json.dumps(report, ensure_ascii=False, allow_nan=False))
        return 0
    except CityJSONImportError as error:
        print(json.dumps({'ok': False, 'message': str(error)[:500]})); return 1
    except Exception:
        print(json.dumps({'ok': False, 'message': 'The selected CityJSON geometry could not be converted. Repair it or choose another LoD.'})); return 1


if __name__ == '__main__':
    raise SystemExit(_main(sys.argv))
