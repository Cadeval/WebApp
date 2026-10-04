"""Read-only IFC material metadata and explicitly selected saved assessments.

Only IFC-derived data is cached. User-owned assessment values are attached on
each request, so a shared source cache never contains another user's report.
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import tempfile
from pathlib import Path

import ifcopenshell
import ifcopenshell.util.element
import ifcopenshell.util.unit

from plugins.bim_model_manager.ifc_extractor.material_assessment import file_hash, canonical_record, fields, number

CACHE_VERSION = 'materials-v4'
IFC_GUID = re.compile(r'^[0-3][0-9A-Za-z_$]{21}$')


def _label(name):
    return re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', str(name or '')).replace('_', ' ')


def _value(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, (tuple, list)):
        return [_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _value(v) for k, v in value.items() if k not in ('id', 'type')}
    return str(value)


def _unit_symbol(unit):
    if unit is None:
        return ''
    if unit.is_a('IfcDerivedUnit'):
        return '·'.join(_unit_symbol(e.Unit) + (f'^{e.Exponent}' if e.Exponent != 1 else '')
                        for e in unit.Elements)
    if unit.is_a('IfcMonetaryUnit'):
        return str(unit.Currency)
    symbol = ifcopenshell.util.unit.get_unit_symbol(unit)
    return str(getattr(unit, 'Name', '') or '') if symbol == '?' else symbol


def _properties(entity, model):
    """Keep declared values and units, including inherited type properties."""
    result = []
    # Complex quantities in common ArchiCAD exports currently trigger a
    # KeyError in IfcOpenShell's verbose mode. Read the values and resolve
    # property entities ourselves so valid sets are retained with their units.
    psets = ifcopenshell.util.element.get_psets(entity, verbose=False)
    inherited = ifcopenshell.util.element.get_type(entity)
    inherited_definitions = getattr(inherited, 'HasPropertySets', None) or ()
    for set_name, values in psets.items():
        try:
            definition = model.by_id(values['id'])
            definitions = [definition]
            if inherited:
                definitions = [d for d in inherited_definitions if d.Name == set_name] + definitions
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
            definitions = []
        entities = {}
        for definition in definitions:
            entities.update({p.Name: p for p in (getattr(definition, 'HasProperties', None)
                or getattr(definition, 'Properties', None) or getattr(definition, 'Quantities', None) or ())})

        def append_values(entries, property_entities, prefix=''):
            for name, value in entries.items():
                if name == 'id':
                    continue
                prop = property_entities.get(name)
                label = prefix + _label(name)
                if isinstance(value, dict) and isinstance(value.get('properties'), dict):
                    children = (getattr(prop, 'HasQuantities', None) or getattr(prop, 'HasProperties', None) or ()) if prop else ()
                    append_values(value['properties'], {p.Name: p for p in children}, label + ' / ')
                    continue
                unit = ''
                if prop is not None:
                    try:
                        unit = _unit_symbol(ifcopenshell.util.unit.get_property_unit(prop, model, use_cache=True))
                    except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
                        pass  # An invalid/missing unit stays unspecified, never inferred.
                result.append({'label': label, 'value': _value(value), 'unit': unit,
                               'set': str(set_name or 'IFC properties'), 'source': 'IFC'})

        append_values(values, entities)
    return result


def _material_members(association):
    if association is None:
        return []
    kind = association.is_a()
    if kind == 'IfcMaterial':
        return [(association, 'single', None)]
    if kind == 'IfcMaterialList':
        return [(m, 'list', None) for m in association.Materials or () if m]
    if kind == 'IfcMaterialLayerSet':
        return [(layer.Material, 'layer', layer) for layer in association.MaterialLayers or () if layer.Material]
    if kind == 'IfcMaterialConstituentSet':
        return [(c.Material, 'constituent', c) for c in association.MaterialConstituents or () if c.Material]
    if kind == 'IfcMaterialProfileSet':
        return [(p.Material, 'profile', p) for p in association.MaterialProfiles or () if p.Material]
    # Handle legal usages and imperfect exports without changing the IFC.
    if kind == 'IfcMaterialLayerSetUsage':
        return _material_members(association.ForLayerSet)
    if kind == 'IfcMaterialProfileSetUsage':
        return _material_members(association.ForProfileSet)
    return []


def extract_source_materials(model, source_hash, *, element_id=None, summary=False):
    elements = {}
    warnings = []
    material_cache = {}
    try:
        length_unit = _unit_symbol(ifcopenshell.util.unit.get_project_unit(model, 'LENGTHUNIT', use_cache=True))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        length_unit = ''
    if element_id:
        try:
            product = model.by_guid(element_id)
        except (RuntimeError, KeyError):
            product = None
        products = [product] if product is not None and product.is_a('IfcProduct') else []
    else:
        products = model.by_type('IfcProduct')
    for element in products:
        identifier = getattr(element, 'GlobalId', None)
        if not identifier:
            continue
        record = {'name': str(getattr(element, 'Name', '') or element.is_a()),
                  'ifc_type': element.is_a(), 'materials': [], 'properties': []}
        elements[identifier] = record
        if not summary:
            try:
                record['properties'] = _properties(element, model)
            except (AttributeError, KeyError, IndexError, RuntimeError, TypeError, ValueError):
                warnings.append({'element_id': identifier, 'message': 'Some IFC properties are unreadable.'})
        try:
            association = ifcopenshell.util.element.get_material(element, should_skip_usage=True)
            members = _material_members(association)
        except (AttributeError, KeyError, IndexError, RuntimeError, TypeError, ValueError):
            warnings.append({'element_id': identifier, 'message': 'The IFC material association is unreadable.'})
            members = []
        by_entity = {}
        for index, (material, kind, member) in enumerate(members, 1):
            name = str(getattr(material, 'Name', '') or 'Unnamed IFC material')
            if not summary and material.id() not in material_cache:
                try:
                    material_cache[material.id()] = _properties(material, model)
                except (AttributeError, KeyError, IndexError, RuntimeError, TypeError, ValueError):
                    material_cache[material.id()] = []
                    warnings.append({'element_id': identifier, 'message': f'Properties for {name} are unreadable.'})
            if material.id() not in by_entity:
                item = {'name': name, 'material_id': material.id(), 'association': kind,
                        'properties': copy.deepcopy(material_cache.get(material.id(), []))}
                if not summary:
                    item['member_contexts'] = []
                for key in (() if summary else ('Description', 'Category')):
                    value = getattr(material, key, None)
                    if value:
                        item['properties'].append({'label': key, 'value': str(value), 'unit': '', 'source': 'IFC', 'set': 'Material'})
                by_entity[material.id()] = item
                record['materials'].append(item)
            item = by_entity[material.id()]
            if not summary:
                item['member_contexts'].append({'association': kind,
                    'association_id': association.id(), 'member_id': member.id() if member is not None else material.id(),
                    'index': index})
            if member is not None and not summary:
                set_name = f'Material {kind} {index}'
                if getattr(member, 'Name', None):
                    item['properties'].append({'label': f'{kind.title()} name', 'value': str(member.Name),
                                               'unit': '', 'source': 'IFC', 'set': set_name})
                thickness = number(getattr(member, 'LayerThickness', None))
                if thickness is not None:
                    item['properties'].append({'label': 'Layer thickness', 'value': thickness,
                                               'unit': length_unit, 'source': 'IFC', 'set': set_name})
                fraction = number(getattr(member, 'Fraction', None))
                if fraction is not None:
                    item['properties'].append({'label': 'Declared fraction', 'value': fraction,
                                               'unit': '', 'source': 'IFC', 'set': set_name})
    return {'schema_version': 1, 'mode': 'summary' if summary else 'detail', 'source': {'sha256': source_hash, 'schema': model.schema,
            'status': 'partial' if warnings else 'complete', 'warnings': warnings}, 'elements': elements}


def source_materials(source, cache_directory, *, element_id=None):
    """Cache a compact index or one selected product, independent of geometry.

    Real house exports contain hundreds of megabytes of repeated property
    sets. The initial viewer fetch therefore includes only material names;
    element detail is extracted and cached when selected.
    """
    if element_id and not IFC_GUID.fullmatch(element_id):
        raise ValueError('Invalid IFC element identifier.')
    source = Path(source)
    digest = file_hash(source)
    cache = Path(cache_directory)
    cache.mkdir(parents=True, exist_ok=True)
    suffix = f'-{element_id}' if element_id else '-index'
    destination = cache / f'{CACHE_VERSION}-{digest}{suffix}.json'
    if destination.exists():
        try:
            data = json.loads(destination.read_text(encoding='utf-8'))
            if data.get('schema_version') == 1 and data.get('source', {}).get('sha256') == digest:
                return data
        except (OSError, ValueError, AttributeError):
            pass
    model = ifcopenshell.open(str(source))
    data = extract_source_materials(model, digest, element_id=element_id, summary=not element_id)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.json', dir=cache,
                                     delete=False) as output:
        temporary = Path(output.name)
        try:
            json.dump(data, output, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            output.flush()
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return data


METRICS = (
    ('volume', 'Volume', 'm³'), ('area', 'Area', 'm²'), ('length', 'Length', 'm'),
    ('mass', 'Installed mass', 'kg'), ('mass_observation', 'Mass incl. replacements', 'kg'),
    ('waste_mass', 'Waste mass', 'kg'), ('recyclable_mass', 'Recyclable mass', 'kg'),
    ('recycling_grade', 'Recovery grade', '1–5'), ('service_life', 'Service life', 'years'),
    ('replacements', 'Replacements', ''),
    ('gwp_a1_a3', 'GWP A1–A3', 'kg CO₂e'), ('gwp_a1_a3_b4', 'GWP A1–A3 + B4', 'kg CO₂e'),
    ('ap_a1_a3', 'AP A1–A3', 'kg SO₂e'), ('ap_a1_a3_b4', 'AP A1–A3 + B4', 'kg SO₂e'),
    ('penrt_a1_a3', 'PENRT A1–A3', 'MJ'), ('penrt_a1_a3_b4', 'PENRT A1–A3 + B4', 'MJ'),
    ('global_brutto_price', 'Global gross material cost', 'EUR'),
    ('local_brutto_price', 'Local gross material cost', 'EUR'),
    ('local_netto_price', 'Local net material cost', 'EUR'),
)


def _assessment_properties(values, *, scope):
    return [{'key': key, 'label': label, 'value': number(values.get(key)), 'unit': unit,
             'source': 'Saved assessment', 'scope': scope} for key, label, unit in METRICS]


def _component_assessment(row, configuration):
    props = _assessment_properties(row, scope='element material component')
    try:
        coefficients = fields(canonical_record(configuration.get(row.get('material'), {})))
    except (AttributeError, TypeError, ValueError):
        coefficients = {}
    for key, label, unit in (
        ('dichte', 'Density', 'kg/m³'), ('gwp', 'Specific GWP', 'kg CO₂e/kg'),
        ('ap', 'Specific AP', 'kg SO₂e/kg'), ('penrt', 'Specific PENRT', 'MJ/kg'),
    ):
        props.insert(0, {'key': key, 'label': label, 'value': number(coefficients.get(key)),
                         'unit': unit, 'source': 'Saved assessment reference', 'scope': 'material coefficient'})
    issues = [str(i) for i in row.get('issues', [])]
    return {'status': 'partial' if issues else 'complete', 'scope': 'element material component',
            'properties': props, 'issues': issues, 'quantity_source': row.get('quantity_source', '')}


def attach_assessment(data, report):
    """Attach element/component values, never whole-building material totals."""
    data = copy.deepcopy(data)
    if not report:
        return data
    configuration = report.get('provenance', {}).get('configuration', {})
    excluded = {entry.get('element_id') for entry in report.get('excluded', []) if isinstance(entry, dict)}
    for identifier, element in data['elements'].items():
        message = ('This part was excluded by the selected assessment method.' if identifier in excluded
                   else 'This part has no assessed component quantities in the selected report.')
        element['assessment'] = {'status': 'excluded' if identifier in excluded else 'unavailable',
            'scope': 'element total', 'properties': _assessment_properties({}, scope='element total'), 'issues': [message]}
        for material in element['materials']:
            material['assessment'] = _component_assessment({'material': material['name'], 'issues': [message]}, configuration)
            material['assessment']['status'] = 'unavailable'
    for row in report.get('rows', []):
        if not isinstance(row, dict):
            continue
        element = data['elements'].get(row.get('element_id'))
        if element is None:
            continue
        matches = [m for m in element['materials'] if m['name'] == row.get('material')
                   and m.get('association') != 'assessment']
        # Assessment rows group by material name, while IFC declarations are
        # separate entities. Preserve conflicting declarations and show their
        # combined name-level calculation once, without assigning it to an
        # arbitrary source entity or repeating its quantities on each card.
        if len(matches) != 1:
            material = {'name': str(row.get('material') or 'Unspecified material'), 'material_id': None,
                        'association': 'assessment', 'properties': [], 'member_contexts': [],
                        'source_material_ids': [m['material_id'] for m in matches]}
            element['materials'].append(material)
        else:
            material = matches[0]
        material['assessment'] = _component_assessment(row, configuration)
        if len(matches) > 1:
            material['assessment']['scope'] = 'element material name group'
            for prop in material['assessment']['properties']:
                if prop['scope'] == 'element material component':
                    prop['scope'] = 'element material name group'
            for declaration in matches:
                declaration['assessment'] = {'status': 'shared', 'scope': 'IFC material declaration',
                    'properties': [], 'issues': [],
                    'note': 'Combined assessment values for this material name are shown once in its material name group.'}
    for identifier, values in report.get('elements', {}).items():
        if identifier in data['elements'] and isinstance(values, dict):
            element = data['elements'][identifier]
            element['assessment'] = {'status': 'complete' if values.get('complete') else 'partial',
                'scope': 'element total', 'properties': _assessment_properties(values, scope='element total'),
                'issues': [issue for material in element['materials'] for issue in material.get('assessment', {}).get('issues', [])]}
    return data
