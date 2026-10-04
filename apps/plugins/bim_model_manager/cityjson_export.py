"""Tessellated IFC components in CityJSON 1.1, without invented georeferencing.

This is an exchange of the detailed BIM geometry, not an envelope extraction or
an assertion that the source forms watertight solids. Original IFC stays intact.
"""
from collections import Counter
import json
from functools import lru_cache
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element
import ifcopenshell.util.geolocation
import ifcopenshell.util.unit
import numpy as np
from jsonschema import Draft7Validator
from referencing import Registry, Resource

from apps.plugins.bim_model_manager.building_locations import _georeference, _unit_metres
from apps.plugins.bim_model_manager.ifc_extractor.material_assessment import file_hash

EXPORT_VERSION = "components-v2"


class CityJSONExportError(ValueError):
    pass


@lru_cache(maxsize=1)
def _schema_validators():
    folder = Path(__file__).with_name("cityjson_schemas")
    schemas = [json.loads(path.read_text()) for path in folder.glob("*.json")]
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)
    main = next(s for s in schemas if s["$id"].endswith("/cityjson.schema.json"))
    object_schema = next(s for s in schemas if s['$id'].endswith('/cityobjects.schema.json'))
    kinds = {'Building', 'BuildingPart', 'BuildingRoom', 'BuildingFurniture',
             'BuildingConstructiveElement', 'BuildingInstallation', 'OtherConstruction'}
    return Draft7Validator(main, registry=registry), {
        kind: Draft7Validator({'$ref': object_schema['$id'] + '#/' + kind}, registry=registry)
        for kind in kinds}


def validate_export(document):
    """Validate the full export with official schemas and explicit type dispatch.

    CityObject type enums in the official root oneOf are disjoint. Selecting
    the declared type validates the same definition, including its geometry,
    without rechecking huge meshes for every other CityObject candidate.
    """
    root, objects = _schema_validators()
    root.validate({**document, 'CityObjects': {}})
    if not isinstance(document.get('CityObjects'), dict):
        raise CityJSONExportError('The exported CityObjects collection is invalid.')
    for obj in document['CityObjects'].values():
        if not isinstance(obj, dict) or obj.get('type') not in objects:
            raise CityJSONExportError('The exported CityObject type is unsupported.')
        objects[obj['type']].validate(obj)
    count = len(document["vertices"])
    for identifier, obj in document["CityObjects"].items():
        for parent in obj.get("parents", []):
            if parent not in document['CityObjects'] or identifier not in document["CityObjects"][parent].get("children", []):
                raise CityJSONExportError("The exported building hierarchy is inconsistent.")
        for child in obj.get('children', []):
            if child not in document['CityObjects'] or identifier not in document['CityObjects'][child].get('parents', []):
                raise CityJSONExportError('The exported building hierarchy is inconsistent.')
        for geometry in obj.get("geometry", []):
            for face in geometry["boundaries"]:
                for ring in face:
                    if any(not isinstance(i, int) or i < 0 or i >= count for i in ring):
                        raise CityJSONExportError("The exported geometry has an invalid vertex reference.")
    # A schema-valid reciprocal cycle still has no valid spatial hierarchy.
    visited, active = set(), set()
    def visit(identifier):
        if identifier in active:
            raise CityJSONExportError('The exported building hierarchy contains a cycle.')
        if identifier in visited:
            return
        active.add(identifier)
        for child in document['CityObjects'][identifier].get('children', []):
            visit(child)
        active.remove(identifier); visited.add(identifier)
    for identifier in document['CityObjects']:
        visit(identifier)


def _attributes(element):
    attributes = {"ifcGuid": element.GlobalId, "ifcClass": element.is_a(), 'ifcStepId': element.id(),
                  "name": element.Name or element.GlobalId or element.is_a()}
    try:
        materials = ifcopenshell.util.element.get_materials(element)
    except (AttributeError, RuntimeError, ValueError, TypeError):
        materials = []
        attributes['ifcMaterialStatus'] = 'unreadable'
    names = []
    for material in materials:
        name = str(getattr(material, 'Name', None) or '').strip()
        if len(name) > 512:
            attributes['ifcMaterialStatus'] = 'bounded list'
            name = name[:512]
        if name and name not in names:
            names.append(name)
            if len(names) >= 128:
                attributes['ifcMaterialStatus'] = 'bounded list'
                break
    if names:
        attributes['ifcMaterials'] = names
        attributes['ifcMaterial'] = names[0]
    return attributes


def _building(element):
    """Follow containment/decomposition, never attach to an arbitrary building."""
    seen = set()
    while element and element.id() not in seen:
        if element.is_a("IfcBuilding"):
            return element
        seen.add(element.id())
        element = ifcopenshell.util.element.get_parent(element)
    return None


def _coordinate_transform(model):
    geo = ifcopenshell.util.geolocation
    try:
        reference = _georeference(model)
    except (NotImplementedError, AttributeError, RuntimeError, ValueError, TypeError) as error:
        raise CityJSONExportError(str(error)) from error
    if reference is None:
        return lambda points: points, None, "Local IFC coordinates in metres; site latitude/longitude does not define a surveyed model transform."
    conversion, crs, map_unit = reference
    name = str(crs.get("Name") or "")
    match = re.fullmatch(r"EPSG\s*:\s*([1-9]\d{0,6})", name.strip(), re.I)
    if not match:
        raise CityJSONExportError("The IFC has a map conversion without an explicit EPSG CRS. Add a supported CRS before exporting georeferenced geometry.")
    from pyproj import CRS
    try:
        projected = CRS.from_epsg(int(match[1])).to_2d()
    except Exception as error:
        raise CityJSONExportError("The IFC's EPSG coordinate system is not available.") from error
    if not projected.is_projected or len(projected.axis_info) < 2 or any(abs(a.unit_conversion_factor - 1) > 1e-9 for a in projected.axis_info[:2]):
        raise CityJSONExportError("CityJSON export currently supports projected EPSG systems in metres. Reproject other coordinate systems first.")
    try:
        project_unit = ifcopenshell.util.unit.get_project_unit(model, 'LENGTHUNIT')
        unit_scale = _unit_metres(project_unit)
        if model.schema == 'IFC2X3' and crs.get('MapUnit'):
            unit = str(crs['MapUnit']).strip().lower()
            if unit not in {'metre', 'meter', 'm'}:
                raise ValueError('CityJSON export requires map coordinates in metres.')
            map_scale = 1.
        else:
            map_scale = _unit_metres(map_unit or project_unit)
        if not math.isclose(map_scale, 1.):
            raise ValueError('The declared IFC map unit must match the projected EPSG metre unit.')
        scale = 1 if conversion.get('Scale') is None else float(conversion['Scale'])
        if not math.isfinite(scale) or scale <= 0:
            raise ValueError('The IFC map conversion scale must be finite and positive.')
        if conversion.get('Scale') is None and not math.isclose(unit_scale, map_scale):
            raise ValueError('Different IFC project and map units require an explicit conversion scale.')
        for field in ('Eastings', 'Northings'):
            if not math.isfinite(float(conversion.get(field))):
                raise ValueError('The IFC map conversion needs finite eastings and northings.')
        for field in ('OrthogonalHeight', 'XAxisAbscissa', 'XAxisOrdinate', 'FactorX', 'FactorY', 'FactorZ'):
            if conversion.get(field) is not None and not math.isfinite(float(conversion[field])):
                raise ValueError('The IFC map conversion contains a non-finite value.')
        if any(conversion.get(field) is not None and conversion[field] <= 0 for field in ('FactorX', 'FactorY', 'FactorZ')):
            raise ValueError('The IFC map conversion axis scales must be positive.')
        if (conversion.get('XAxisAbscissa') is not None or conversion.get('XAxisOrdinate') is not None) and not (conversion.get('XAxisAbscissa') or conversion.get('XAxisOrdinate')):
            raise ValueError('The IFC map conversion axis has no direction.')
    except (AttributeError, RuntimeError, ValueError, TypeError) as error:
        raise CityJSONExportError(str(error)) from error
    # Native meshes use SI; the IFC geolocation API expects project units.
    origin = np.array(geo.auto_xyz2enh(model, 0, 0, 0))
    basis = np.column_stack([np.array(geo.auto_xyz2enh(model, *axis)) - origin for axis in np.eye(3)])
    if not np.isfinite(origin).all() or not np.isfinite(basis).all():
        raise CityJSONExportError("The IFC map conversion contains non-finite values.")
    return lambda points: (points / unit_scale) @ basis.T + origin, f"https://www.opengis.net/def/crs/EPSG/0/{match[1]}", "Coordinates follow the IFC map conversion and declared EPSG CRS."


def export_components(source, destination):
    """Worker entry point. Native geometry uses up to four parallel threads."""
    source, destination = Path(source), Path(destination)
    source_digest = file_hash(source)
    model = ifcopenshell.open(str(source))
    transform, reference_system, coordinate_notice = _coordinate_transform(model)
    products = model.by_type('IfcProduct')
    guid_counts = Counter(element.GlobalId for element in products)
    identifiers = {element.id(): (element.GlobalId if element.GlobalId and guid_counts[element.GlobalId] == 1
                   else f'{element.GlobalId or "ifc"}-step-{element.id()}') for element in products}
    buildings = model.by_type('IfcBuilding')
    objects = {identifiers[b.id()]: {"type": "Building", "attributes": _attributes(b), "children": []}
               for b in buildings}
    for building in buildings:
        parent = _building(ifcopenshell.util.element.get_parent(building))
        if parent and parent != building:
            identifier, parent_id = identifiers[building.id()], identifiers[parent.id()]
            objects[identifier].update(type='BuildingPart', parents=[parent_id])
            objects[parent_id]['children'].append(identifier)
    vertices, vertex_index = [], {}
    rendered, rendered_steps, skipped_degenerate = set(), set(), 0
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    settings.set("weld-vertices", True)
    iterator = ifcopenshell.geom.iterator(settings, model, min(os.cpu_count() or 1, 4))
    if iterator.initialize():
        while True:
            shape = iterator.get()
            element = model.by_id(shape.id)
            if not element.is_a("IfcOpeningElement"):
                points = transform(np.array(shape.geometry.verts).reshape((-1, 3)))
                if not np.isfinite(points).all():
                    raise CityJSONExportError("An IFC component contains non-finite coordinates.")
                indices = []
                for point in points:
                    # Millimetre precision, shared integer coordinates required by 1.1.
                    key = tuple(int(round(float(v) * 1000)) for v in point)
                    if key not in vertex_index:
                        vertex_index[key] = len(vertices)
                        vertices.append(list(key))
                    indices.append(vertex_index[key])
                faces = []
                for face in np.array(shape.geometry.faces).reshape((-1, 3)):
                    ring = [indices[int(v)] for v in face]
                    a, b, c = (vertices[index] for index in ring)
                    ab, ac = ([b[i]-a[i] for i in range(3)], [c[i]-a[i] for i in range(3)])
                    cross = (ab[1]*ac[2]-ab[2]*ac[1], ab[2]*ac[0]-ab[0]*ac[2], ab[0]*ac[1]-ab[1]*ac[0])
                    if len(set(ring)) < 3 or not any(cross):
                        skipped_degenerate += 1
                    else:
                        faces.append([ring])
                if faces:
                    building = _building(element)
                    attributes = _attributes(element)
                    identifier = identifiers[element.id()]
                    if element.is_a("IfcBuilding"):
                        obj = objects[identifier]
                    elif building:
                        kind = ("BuildingRoom" if element.is_a("IfcSpace") else "BuildingFurniture" if element.is_a("IfcFurnishingElement")
                                else 'BuildingInstallation' if element.is_a('IfcDistributionElement')
                                else "BuildingConstructiveElement" if element.is_a('IfcElement') else 'OtherConstruction')
                        parent_id = identifiers[building.id()]
                        obj = {"type": kind, "parents": [parent_id], "attributes": attributes}
                        objects[identifier] = obj
                        if identifier not in objects[parent_id]['children']:
                            objects[parent_id]["children"].append(identifier)
                    else:
                        obj = {"type": "OtherConstruction", "attributes": attributes}
                        objects[identifier] = obj
                    obj.setdefault("geometry", []).append({"type": "MultiSurface", "lod": "3", "boundaries": faces})
                    rendered.add(element.GlobalId)
                    rendered_steps.add(element.id())
            if len(vertices) > 2_000_000:
                raise CityJSONExportError("This model exceeds the CityJSON export geometry limit (2 million vertices). Split it into smaller models.")
            if not iterator.next():
                break
    if not rendered:
        raise CityJSONExportError("The IFC contains no geometry that can be exported.")
    expected = {e.id(): e for e in products if e.Representation and not e.is_a('IfcOpeningElement')}
    omitted = [expected[identifier] for identifier in sorted(set(expected) - rendered_steps)]
    warnings = [coordinate_notice, "Detailed triangulated components at LoD 3; no watertight exterior shell or generalized city envelope is inferred.",
                "IFC material names are retained as attributes; textures, property sets, LCA and cost reports remain in the original IFC and Cadevil assessment."]
    if omitted:
        warnings.append(f"{len(omitted)} represented IFC elements could not be tessellated; their GlobalIds and STEP IDs are listed in the export provenance.")
    if skipped_degenerate:
        warnings.append(f"{skipped_degenerate} triangles collapsed at millimetre precision and were omitted.")
    duplicate_guids = sum(count > 1 for count in guid_counts.values())
    if duplicate_guids:
        warnings.append(f'{duplicate_guids} repeated IFC GlobalIds were disambiguated with STEP IDs; original identifiers remain in attributes.')
    empty_buildings = [identifier for identifier, obj in objects.items()
                       if obj['type'] in {'Building', 'BuildingPart'} and not obj.get('children') and not obj.get('geometry')]
    if empty_buildings:
        warnings.append(f'{len(empty_buildings)} empty IFC buildings were retained without inventing geometry.')
    values = np.array(vertices, dtype=float) * .001
    projects = model.by_type('IfcProject')
    title = str(projects[0].Name or 'IFC component export')[:512] if len(projects) == 1 else 'IFC component export'
    document = {"type": "CityJSON", "version": "1.1", "transform": {"scale": [.001] * 3, "translate": [0.0] * 3},
                "CityObjects": objects, "vertices": vertices,
                # Content caches may be shared by identical uploads. Use only
                # IFC content here; caller-owned filenames belong in downloads.
                "metadata": {"title": title, "geographicalExtent": [*values.min(axis=0).tolist(), *values.max(axis=0).tolist()]},
                "cadevil": {"converter": EXPORT_VERSION, "sourceSha256": source_digest,
                            "warnings": warnings, "omittedIfcGuids": sorted({e.GlobalId for e in omitted if e.GlobalId}),
                            'omittedIfcElements': [{'guid': e.GlobalId, 'stepId': e.id(), 'ifcClass': e.is_a()} for e in omitted],
                            'emptyBuildings': empty_buildings}}
    if reference_system:
        document["metadata"]["referenceSystem"] = reference_system
    validate_export(document)
    if file_hash(source) != source_digest:
        raise CityJSONExportError('The IFC source changed during export. Retry with a stable source.')
    destination.write_text(json.dumps(document, ensure_ascii=False, separators=(",", ":"), allow_nan=False))


def model_cityjson(source, cache_directory, *, timeout_seconds=180):
    """Cache by source bytes; isolate native work and publish only complete exports."""
    source, cache = Path(source).resolve(), Path(cache_directory)
    cache.mkdir(parents=True, exist_ok=True)
    digest = file_hash(source)
    destination = cache / f"{EXPORT_VERSION}-{digest}.city.json"
    if destination.exists():
        return destination
    with tempfile.TemporaryDirectory(prefix="cityjson-export-", dir=cache) as folder:
        output = Path(folder) / "model.city.json"
        try:
            result = subprocess.run([sys.executable, "-m", "apps.plugins.bim_model_manager.cityjson_export", str(source), str(output)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout_seconds,
                                    cwd=Path(__file__).resolve().parents[3], check=False)
        except subprocess.TimeoutExpired as error:
            raise CityJSONExportError("CityJSON conversion exceeded three minutes. Split the model and try again.") from error
        if result.returncode or not output.exists():
            # Worker emits only our safe actionable errors; native diagnostics stay in logs.
            message = result.stderr.decode("utf-8", errors="replace")
            safe = next((line[6:] for line in message.splitlines() if line.startswith("ERROR:")), None)
            raise CityJSONExportError(safe or "The IFC could not be converted to CityJSON. Check its geometry and georeferencing.")
        if file_hash(source) != digest:
            raise CityJSONExportError('The IFC source changed during export. Retry with a stable source.')
        os.replace(output, destination)
    return destination


if __name__ == "__main__":
    try:
        export_components(Path(sys.argv[1]), Path(sys.argv[2]))
    except CityJSONExportError as error:
        print("ERROR:" + str(error), file=sys.stderr)
        sys.exit(1)
