"""Read-only building map anchors from explicitly declared IFC georeferencing.

Site latitude/longitude identifies the site placement origin, not a surveyed
building centroid. Projected anchors identify the building placement origin.
Neither local engineering coordinates nor a guessed CRS becomes latitude.
Ownership belongs in the caller: resolve FileUpload(user=request.user) before
passing its stored source to this module.
"""
from collections import Counter
import json
import math
import os
from pathlib import Path
import re
import tempfile

import ifcopenshell
import ifcopenshell.util.element
import ifcopenshell.util.geolocation as geolocation
import ifcopenshell.util.placement
import ifcopenshell.util.unit

from apps.plugins.bim_model_manager.ifc_extractor.material_assessment import file_hash

_EPSG = re.compile(r"EPSG\s*:\s*([1-9][0-9]{0,6})", re.I)
_GUID = re.compile(r"[0-3][0-9A-Za-z_$]{21}")
_ERRORS = (AttributeError, AssertionError, IndexError, KeyError, RuntimeError,
           TypeError, ValueError, ArithmeticError)


def _finite(value):
    if isinstance(value, bool):
        raise ValueError('Boolean coordinates are invalid.')
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('Coordinates must be finite.')
    return value


def _angle(parts, maximum):
    if not isinstance(parts, (tuple, list)) or len(parts) not in (3, 4):
        raise ValueError('A site reference needs degrees, minutes and seconds.')
    if any(isinstance(p, bool) or not isinstance(p, int) for p in parts):
        raise ValueError('Site angle components must be integers.')
    if abs(parts[1]) >= 60 or abs(parts[2]) >= 60 or (len(parts) == 4 and abs(parts[3]) >= 1_000_000):
        raise ValueError('Site angle components are out of range.')
    result = geolocation.dms2dd(*parts)
    if not -maximum <= result <= maximum:
        raise ValueError('The site reference is outside geographic bounds.')
    return result


def _site(building):
    current, visited = building, set()
    for _ in range(128):
        if current.id() in visited:
            raise ValueError('The building spatial hierarchy contains a cycle.')
        visited.add(current.id())
        if current.is_a('IfcSite'):
            return current
        parents = [relation.RelatingObject for relation in getattr(current, 'Decomposes', ())
                   if relation.is_a('IfcRelAggregates') and relation.RelatingObject]
        if len(parents) > 1:
            raise ValueError('The building belongs to multiple spatial parents.')
        if not parents:
            return None
        current = parents[0]
    raise ValueError('The building spatial hierarchy is too deep.')


def _unit_metres(unit, depth=0):
    if depth > 8 or not unit or getattr(unit, 'UnitType', None) != 'LENGTHUNIT':
        raise ValueError('A declared length unit is required.')
    if unit.is_a('IfcSIUnit') and unit.Name == 'METRE':
        return ifcopenshell.util.unit.get_prefix_multiplier(unit.Prefix)
    if unit.is_a('IfcConversionBasedUnit'):
        if getattr(unit, 'ConversionOffset', 0):
            raise ValueError('Offset length units are unsupported.')
        factor = unit.ConversionFactor
        result = _finite(factor.ValueComponent.wrappedValue) * _unit_metres(factor.UnitComponent, depth + 1)
        if result > 0:
            return result
    raise ValueError('The declared length unit is unsupported.')


def _georeference(model):
    """Return one unambiguous map conversion; never accept an arbitrary first."""
    if model.schema == 'IFC2X3':
        projects = model.by_type('IfcProject')
        if len(projects) != 1:
            return None
        conversion = ifcopenshell.util.element.get_pset(projects[0], 'ePSet_MapConversion')
        if not conversion:
            return None
        return conversion, ifcopenshell.util.element.get_pset(projects[0], 'ePSet_ProjectedCRS') or {}, None
    operations = model.by_type('IfcCoordinateOperation')
    if not operations:
        return None
    if len(operations) != 1 or not operations[0].is_a('IfcMapConversion'):
        raise NotImplementedError('The IFC has multiple or unsupported coordinate operations.')
    operation = operations[0]
    if not operation.SourceCRS or not operation.SourceCRS.is_a('IfcGeometricRepresentationContext'):
        raise ValueError('The map conversion has no engineering representation context.')
    if not operation.TargetCRS or not operation.TargetCRS.is_a('IfcProjectedCRS'):
        raise NotImplementedError('The map conversion has no supported target CRS.')
    # auto_xyz2enh uses the model WCS; another context would be ambiguous.
    wcs = geolocation.get_wcs(model)
    source_wcs = ifcopenshell.util.placement.get_axis2placement(operation.SourceCRS.WorldCoordinateSystem)
    import numpy as np
    if wcs is None or not np.allclose(wcs, source_wcs, rtol=0, atol=1e-9):
        raise NotImplementedError('The conversion context differs from the model coordinate context.')
    return operation.get_info(), operation.TargetCRS.get_info(), operation.TargetCRS.MapUnit


def _projected(model, building, conversion, crs_info, map_unit):
    match = _EPSG.fullmatch(str(crs_info.get('Name') or '').strip())
    if not match:
        raise NotImplementedError('The projected CRS needs one explicit EPSG identifier.')
    crs_name = 'EPSG:' + match.group(1)
    for field in ('Eastings', 'Northings'):
        _finite(conversion.get(field))
    _finite(conversion.get('OrthogonalHeight') or 0)
    scale = _finite(1 if conversion.get('Scale') is None else conversion['Scale'])
    if scale <= 0:
        raise ValueError('The map conversion scale must be positive.')
    x, y = conversion.get('XAxisAbscissa'), conversion.get('XAxisOrdinate')
    if x is not None or y is not None:
        if not (_finite(x or 0) or _finite(y or 0)):
            raise ValueError('The map conversion axis must have a direction.')
    for field in ('FactorX', 'FactorY', 'FactorZ'):
        if field in conversion and _finite(conversion[field]) <= 0:
            raise ValueError('Map conversion axis scales must be positive.')
    project_unit = ifcopenshell.util.unit.get_project_unit(model, 'LENGTHUNIT')
    project_metres = _unit_metres(project_unit)
    if model.schema == 'IFC2X3' and crs_info.get('MapUnit'):
        legacy_units = {'metre': 1, 'meter': 1, 'm': 1, 'millimetre': .001,
                        'millimeter': .001, 'mm': .001, 'foot': .3048, 'feet': .3048, 'ft': .3048}
        map_metres = legacy_units.get(str(crs_info['MapUnit']).lower())
        if map_metres is None:
            raise NotImplementedError('The IFC2X3 map unit is unsupported.')
    else:
        map_metres = _unit_metres(map_unit or project_unit)
    if conversion.get('Scale') is None and not math.isclose(project_metres, map_metres):
        raise ValueError('Different project and map units require an explicit conversion scale.')
    placement = building.ObjectPlacement
    if not placement:
        raise NotImplementedError('The building has no placement anchor. Set its location or import a source geometry anchor.')
    if not placement.is_a('IfcLocalPlacement'):
        raise NotImplementedError('This building placement type is unsupported.')
    matrix = ifcopenshell.util.placement.get_local_placement(placement)
    local = tuple(_finite(value) for value in matrix[:3, 3])
    from pyproj import CRS, network
    from pyproj.exceptions import ProjError
    from pyproj.transformer import TransformerGroup
    try:
        if network.is_network_enabled():
            raise NotImplementedError('Coordinate transformations require offline PROJ mode.')
        crs = CRS.from_epsg(int(match.group(1))).to_2d()
        if not crs.is_projected or len(crs.axis_info) < 2:
            raise NotImplementedError('The declared CRS has no projected horizontal length axes.')
        axis_metres = [_finite(axis.unit_conversion_factor) for axis in crs.axis_info[:2]]
        if min(axis_metres) <= 0 or not math.isclose(*axis_metres):
            raise NotImplementedError('The projected coordinate axes use unsupported units.')
        # TransformerGroup excludes missing grids. Disallow ballpark fallback.
        group = TransformerGroup(crs, CRS.from_epsg(4326), always_xy=True, allow_ballpark=False)
        if not group.best_available or not group.transformers:
            raise NotImplementedError('The precise CRS transformation is unavailable locally.')
        transformer = group.transformers[0]
        if any(not grid.available for operation in transformer.operations for grid in operation.grids):
            raise NotImplementedError('Grid-based transformations require an explicitly installed offline grid.')
        # No CRS strings, pipelines, file paths, grids or network URLs supplied by IFC are executed.
        east, north, _ = geolocation.auto_xyz2enh(model, *local, should_return_in_map_units=True)
        factor = map_metres / axis_metres[0]
        longitude, latitude = transformer.transform(_finite(east) * factor, _finite(north) * factor, errcheck=True)
        latitude, longitude = _finite(latitude), _finite(longitude)
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError('The projected location is outside geographic bounds.')
        return latitude, longitude, crs_name
    except ProjError as error:
        raise ValueError('The declared CRS cannot be transformed to WGS84.') from error


def extract_building_locations(model):
    """Return one map row per IfcBuilding, including explicit unavailable rows."""
    buildings = model.by_type('IfcBuilding')
    counts = Counter(getattr(building, 'GlobalId', '') for building in buildings)
    try:
        reference, reference_error = _georeference(model), None
    except (NotImplementedError, *_ERRORS) as error:
        reference, reference_error = None, error
    rows = []
    for building in buildings:
        row = {'guid': str(getattr(building, 'GlobalId', '') or ''),
               'name': str(getattr(building, 'Name', '') or 'Unnamed IFC building'),
               'site_name': '', 'latitude': None, 'longitude': None,
               'source': '', 'status': 'missing', 'message': 'No explicit geographic location is declared.', 'crs': ''}
        rows.append(row)
        try:
            if not _GUID.fullmatch(row['guid']) or counts[row['guid']] != 1:
                raise ValueError('The building needs a unique valid IFC GlobalId.')
            site = _site(building)
            row['site_name'] = str(getattr(site, 'Name', '') or '')
            if reference_error:
                raise reference_error
            if reference:
                row.update(source='projected_crs', crs=str(reference[1].get('Name') or ''))
                latitude, longitude, crs = _projected(model, building, *reference)
                row.update(latitude=latitude, longitude=longitude, crs=crs, status='located',
                           message='Building placement origin transformed from the declared projected CRS.')
            elif site:
                row['source'] = 'ifc_site'
                if site.RefLatitude is None and site.RefLongitude is None:
                    continue
                latitude, longitude = _angle(site.RefLatitude, 90), _angle(site.RefLongitude, 180)
                row.update(latitude=latitude, longitude=longitude, status='located', crs='EPSG:4326',
                           message='Declared IFC site reference; this marks the site origin, not the building centroid.')
        except (ImportError, NotImplementedError) as error:
            row.update(status='unsupported', message=str(error), latitude=None, longitude=None)
        except _ERRORS as error:
            row.update(status='invalid', message=str(error), latitude=None, longitude=None)
    return rows


def source_building_locations(source, cache_directory=None):
    """Cache immutable IFC-derived rows by source content, independently of users.

    The caller must authenticate every read and serve metadata privately. Cache
    directories should be outside publicly mounted MEDIA_ROOT; overrides belong
    in the database and are never written into this shared source cache.
    """
    source = Path(source)
    digest = file_hash(source)
    cache = Path(cache_directory) if cache_directory is not None else None
    destination = cache / f'building-locations-v1-{digest}.json' if cache else None
    if destination and destination.exists():
        try:
            if destination.stat().st_size <= 8 * 1024 * 1024:
                data = json.loads(destination.read_text(encoding='utf-8'))
                if data.get('schema_version') == 1 and data.get('ifc_sha256') == digest and isinstance(data.get('buildings'), list):
                    return data
        except (ValueError, OSError, AttributeError):
            pass
    data = {'schema_version': 1, 'ifc_sha256': digest,
            'buildings': extract_building_locations(ifcopenshell.open(str(source)))}
    if file_hash(source) != digest:
        raise ValueError('The IFC source changed while its locations were read.')
    if destination:
        cache.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=cache, delete=False) as output:
                temporary = Path(output.name)
                json.dump(data, output, ensure_ascii=False, allow_nan=False)
            os.replace(temporary, destination)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
    return data
