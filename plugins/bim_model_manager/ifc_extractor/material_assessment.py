"""Bottom-up material passport for thesis sections 03.4 and 04.3.

Reference columns use kg/m³, kg CO2e/kg, kg SO2e/kg, MJ/kg and
percentages (0..100). No unknown coefficient is converted to zero.
The report keeps element/material rows, omissions and calculation assumptions.
"""
from __future__ import annotations

import csv
import fnmatch
import hashlib
from .recovery_costs import recovery_cost_analysis
from .material_neighbors import material_classification

import json
import math
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path


def number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = ''.join(value.split())
        if not value:
            return None
        if ',' in value and '.' in value:
            value = value.replace('.', '').replace(',', '.') if value.rfind(',') > value.rfind('.') else value.replace(',', '')
        elif value.count(',') == 1:
            value = value.replace(',', '.')
        elif ',' in value:
            # Do not turn malformed multiple decimal separators into a plausible value.
            if not re.fullmatch(r'[+-]?\d{1,3}(,\d{3})+', value):
                return None
            value = value.replace(',', '')
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def field_name(value):
    return ' '.join(str(value).strip().casefold().split())


def fields(config):
    result = {}
    for key, value in config.items():
        normalized = field_name(key)
        if normalized in result:
            raise ValueError(f'Duplicate configuration column: {key}')
        result[normalized] = value
    return result


def canonical_column(label):
    """Map the supplied MP workbook headers and existing short-form headers."""
    key = field_name(label)
    aliases = {'baubook wahl': 'Referenzmaterial', 'brutto': 'Globaler Brutto Preis',
               'brutto (wien)': 'Lokaler Brutto Preis', 'netto (wien)': 'Lokaler Netto Preis'}
    if key in aliases: return aliases[key]
    prefix = 'NEU ' if key.startswith('neu ') else ''
    tail = key.removeprefix('neu ')
    if tail.startswith('dichte'): return 'Dichte'
    if tail.startswith('verwertungspotential'): return prefix + 'Verwertungspotential'
    if tail.startswith('abfallreduktion'): return prefix + 'Abfallreduktion'
    if tail.startswith('anteil recycling') or tail == 'recycling': return prefix + 'Recycling'
    if tail.startswith('gwp'): return 'GWP'
    if tail.startswith('ap ') or tail == 'ap': return 'AP'
    if tail.startswith('penrt'): return 'PENRT'
    if tail.startswith('nutzungsdauer'): return 'Nutzungsdauer'
    return str(label).strip()


def canonical_record(record):
    result = {}
    seen = set()
    for key, value in record.items():
        canonical = canonical_column(key)
        normalized = field_name(canonical)
        if normalized in seen:
            raise ValueError(f'Duplicate configuration column: {key}')
        seen.add(normalized)
        result[canonical] = value
    unit = field_name(result.get('Einheit', '')).replace('€/', '').replace('²','2').replace('³','3')
    if 'Preis Multiplikator' not in result:
        result['Preis Multiplikator'] = {'m3':'Volumen', 'm2':'Fläche', 'm':'Länge', 'kg':'Masse'}.get(unit)
    return result


@dataclass(frozen=True)
class AssessmentOptions:
    years: int = 50
    include_endpoint: bool = False
    grade_weighting: str | None = None
    lca_averaging: str | None = None
    replacement_method: str = 'discrete'
    excluded_classes: tuple[str, ...] = ('IfcWindow', 'IfcDoor', 'IfcWindowStandardCase', 'IfcDoorStandardCase')

    def __post_init__(self):
        if isinstance(self.years, bool) or not isinstance(self.years, int) or self.years <= 0:
            raise ValueError('Observation period must be a positive integer.')
        if self.grade_weighting not in (None, 'mass', 'equal'):
            raise ValueError('Grade weighting must be unspecified, mass or equal.')
        if self.lca_averaging not in (None, 'installed_mass', 'material_mean'):
            raise ValueError('LCA averaging must be unspecified, installed_mass or material_mean.')
        if self.replacement_method not in ('ibo_oi3_2023', 'discrete'):
            raise ValueError('Unknown replacement method.')


def replacement_count(life, options):
    """Complete replacements within the explicitly chosen observation boundary."""
    life = number(life)
    if life is None or life <= 0:
        raise ValueError('Service life must be finite and positive.')
    if options.replacement_method == 'ibo_oi3_2023':
        # IBO OI3 V5.0, September 2023, §5.4.2/5.4.4, pp26–27:
        # ceil((tB - 1)/tN - 1). Clamp long service lives to no replacement.
        return max(0, math.ceil((options.years - 1) / life - 1))
    ratio = options.years / life
    return max(0, math.floor(ratio) if options.include_endpoint else math.ceil(ratio) - 1)


def combination_matches(rule, other_names):
    """Any configured companion triggers the adjustment.

    Lists preserve material names containing commas. Legacy comma-delimited
    strings are accepted. Exact names, family prefixes (e.g. Beton) and explicit
    wildcard patterns can refer to IFC names or the selected reference names.
    This detects co-presence, not physical bonding or geometric separability.
    """
    tokens = rule if isinstance(rule, list) else str(rule or '').split(';')
    names = [field_name(n) for n in other_names]
    for token in tokens:
        token = field_name(token)
        if not token:
            continue
        for name in names:
            if name == token or name.startswith(token + ' ') or name.startswith(token + ',') or name.startswith(token + '.') or fnmatch.fnmatchcase(name, token):
                return True
    return False


def _coefficient(config, key, issues, *, positive=False, minimum=None, maximum=None):
    value = number(config.get(field_name(key)))
    if value is None or (positive and value <= 0) or (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        issues.append(f'Missing or invalid {key}')
        return None
    return value


class MaterialAssessment:
    def __init__(self, config, options=None):
        self.options = options or AssessmentOptions()
        self.config = {name: canonical_record(record) for name, record in config.items()}
        self.rows = []
        self.issues = []
        self.excluded = []
        self.quantity_warnings = []

    def add_issue(self, element_id, message):
        self.issues.append({'element_id': element_id, 'message': str(message)})

    def add_element(self, element_id, ifc_class, components, *, area=None, length=None, quantity_source='IFC component quantity'):
        if ifc_class in self.options.excluded_classes:
            self.excluded.append({'element_id': element_id, 'ifc_class': ifc_class})
            return
        if not components:
            self.add_issue(element_id, 'No material quantities available')
            return
        for name, volume in components.items():
            row = {'element_id': element_id, 'ifc_class': ifc_class, 'material': name,
                   'volume': number(volume), 'area': number(area), 'length': number(length),
                   'quantity_source': quantity_source, 'issues': []}
            self.rows.append(row)
            if row['volume'] is None or row['volume'] < 0:
                row['issues'].append('Missing or invalid component volume')
                continue
            if name not in self.config:
                row['issues'].append('Unmatched material reference')
                continue
            try:
                c = fields(self.config[name])
            except (ValueError, AttributeError) as exc:
                row['issues'].append(str(exc))
                continue
            issues = row['issues']
            others = []
            for other in components:
                if other != name:
                    others.append(other)
                    oc = self.config.get(other, {})
                    if isinstance(oc, dict):
                        reference = fields(oc).get('referenzmaterial')
                        if reference:
                            others.append(str(reference))
            adjusted = combination_matches(c.get('kombination'), others)
            row['combination_adjusted'] = adjusted
            prefix = 'NEU ' if adjusted else ''
            density = _coefficient(c, 'Dichte', issues, positive=True)
            gwp = _coefficient(c, 'GWP', issues)  # Negative biogenic GWP is valid.
            ap = _coefficient(c, 'AP', issues, minimum=0)
            penrt = _coefficient(c, 'PENRT', issues, minimum=0)
            waste = _coefficient(c, prefix + 'Abfallreduktion', issues, minimum=0, maximum=125)
            recycling = _coefficient(c, prefix + 'Recycling', issues, minimum=0, maximum=100)
            grade = _coefficient(c, prefix + 'Verwertungspotential', issues, minimum=1, maximum=5)
            if waste is not None and recycling is not None and waste <= 100 and waste + recycling > 100 + 1e-8:
                issues.append('Waste and recycling percentages exceed 100 in total')
                waste = recycling = None
            life = _coefficient(c, 'Nutzungsdauer', issues, positive=True)
            replacements = replacement_count(life, self.options) if life is not None else None
            row.update(service_life=life, replacements=replacements, recycling_grade=grade)
            mass = row['volume'] * density if density is not None else None
            row['mass'] = mass
            row['mass_observation'] = mass * (1 + replacements) if mass is not None and replacements is not None else None
            for label, rate in [('waste_mass', waste), ('recyclable_mass', recycling)]:
                row[label] = mass * rate / 100 if mass is not None and rate is not None else None
                row[label + '_observation'] = row['mass_observation'] * rate / 100 if row['mass_observation'] is not None and rate is not None else None
            for indicator, coefficient in [('gwp', gwp), ('ap', ap), ('penrt', penrt)]:
                initial = mass * coefficient if mass is not None and coefficient is not None else None
                row[indicator + '_a1_a3'] = initial
                row[indicator + '_b4'] = initial * replacements if initial is not None and replacements is not None else None
                row[indicator + '_a1_a3_b4'] = initial * (1 + replacements) if initial is not None and replacements is not None else None
            if self.options.replacement_method == 'ibo_oi3_2023' and replacements:
                fossil = number(c.get('gwp fossil'))
                biogenic = number(c.get('gwp biogen'))
                if fossil is None or biogenic is None:
                    row['gwp_b4'] = row['gwp_a1_a3_b4'] = None
                    issues.append('OI3 replacement GWP requires fossil and biogenic coefficients; total GWP alone is insufficient')
                elif gwp is not None and not math.isclose(fossil+biogenic,gwp,rel_tol=0.001,abs_tol=1e-8):
                    row['gwp_b4'] = row['gwp_a1_a3_b4'] = None
                    issues.append('Fossil plus biogenic GWP does not reconcile with total GWP')
                elif mass is not None:
                    row['gwp_b4'] = mass*fossil*replacements
                    row['gwp_a1_a3_b4'] = row['gwp_a1_a3']+row['gwp_b4']
            basis = field_name(c.get('preis multiplikator', ''))
            quantities = {'volumen': row['volume'], 'fläche': number(area), 'flaeche': number(area), 'länge': number(length), 'laenge': number(length), 'masse': mass}
            multiplier = quantities.get(basis)
            row['price_basis'] = basis
            for label, key in [('global_brutto_price', 'Globaler Brutto Preis'), ('local_brutto_price', 'Lokaler Brutto Preis'), ('local_netto_price', 'Lokaler Netto Preis')]:
                price = number(c.get(field_name(key)))
                row[label] = price * multiplier if price is not None and price >= 0 and multiplier is not None and multiplier >= 0 else None
            if multiplier is None:
                issues.append('Missing or invalid price quantity basis')
            elif any(row[key] is None for key in ('global_brutto_price', 'local_brutto_price', 'local_netto_price')):
                issues.append('Missing or invalid unit price')

    def report(self):
        summed = ['volume', 'area', 'length', 'mass', 'mass_observation', 'waste_mass', 'recyclable_mass',
                  'waste_mass_observation', 'recyclable_mass_observation', 'global_brutto_price',
                  'local_brutto_price', 'local_netto_price']
        summed += [i + '_' + period for i in ('gwp', 'ap', 'penrt') for period in ('a1_a3', 'b4', 'a1_a3_b4')]
        groups = defaultdict(list)
        element_groups = defaultdict(list)
        for row in self.rows:
            groups[row['material']].append(row)
            element_groups[row['element_id']].append(row)
        def aggregate(rows):
            result = {}
            for key in summed:
                values = [r.get(key) for r in rows]
                result[key] = sum(values) if all(v is not None for v in values) else None
                result[key + '_known_subtotal'] = sum(v for v in values if v is not None)
            weights = [(r.get('recycling_grade'), r.get('mass')) for r in rows]
            if weights and all(g is not None and m is not None for g, m in weights) and sum(m for _, m in weights) > 0:
                result['recycling_grade'] = sum(g*m for g,m in weights)/sum(m for _,m in weights)
            else:
                result['recycling_grade'] = None
            return result
        materials = {name: aggregate(rows) for name, rows in sorted(groups.items())}
        elements = {identifier: dict(aggregate(rows), ifc_class=rows[0]['ifc_class'],
                    materials=sorted({r['material'] for r in rows}),
                    complete=not any(r['issues'] for r in rows))
                    for identifier, rows in sorted(element_groups.items())}
        total = aggregate(self.rows)
        if self.options.grade_weighting == 'equal':
            grades = [m['recycling_grade'] for m in materials.values()]
            total['recycling_grade'] = sum(grades)/len(grades) if grades and all(g is not None for g in grades) else None
        elif self.options.grade_weighting is None:
            total['recycling_grade'] = None
        if self.issues:
            # An omitted element cannot silently become a zero contribution.
            for key in summed:
                total[key] = None
            total['recycling_grade'] = None
        # Empty inventory is unavailable, never a zero-impact building.
        if not self.rows:
            total = {key: None for key in total}
        averages = {'method':self.options.lca_averaging, 'denominator':None, 'values':{}}
        if self.options.lca_averaging == 'installed_mass':
            averages['denominator'] = total.get('mass')
            averages['note'] = 'Both periods are divided by installed mass at year 0; observation-period values retain replacement production impacts per initially installed kg.'
            averages['unit_basis'] = 'per installed kg'
        elif self.options.lca_averaging == 'material_mean':
            averages['denominator'] = len(materials) if materials and not self.issues else None
            averages['note'] = 'Arithmetic mean of aggregated impact totals across distinct assessed material names. This is not a mass-specific coefficient.'
            averages['unit_basis'] = 'per distinct material'
        else:
            averages['note'] = 'No averaging denominator selected; thesis wording alone does not define it.'
            averages['unit_basis'] = 'unspecified'
        for indicator in ('gwp', 'ap', 'penrt'):
            for period in ('a1_a3','a1_a3_b4'):
                key = indicator + '_' + period
                denominator = averages['denominator']
                value = total.get(key)
                averages['values'][key] = value / denominator if value is not None and denominator is not None and denominator > 0 else None
        return {'material_classification':material_classification(self.rows,self.config), 'recovery_cost_analysis': recovery_cost_analysis(self.rows), 'schema_version': 2, 'lca_averages':averages, 'options': asdict(self.options), 'units': {
                    'volume': 'm³', 'mass': 'kg', 'gwp': 'kg CO₂e', 'ap': 'kg SO₂e', 'penrt': 'MJ',
                    'recovery_inputs': 'percent'},
                'rows': self.rows, 'materials': materials, 'elements': elements, 'building': total,
                'issues': self.issues, 'excluded': self.excluded,
                'quantity_warnings': self.quantity_warnings,
                'complete': bool(self.rows) and not self.issues and not any(r['issues'] for r in self.rows),
                'method': 'Thesis §03.4: A1–A3 plus production associated with full material replacements in B4. No transport, operation or end-of-life impact coefficients. This simplified MP is not an OI3 or EI10 certification.',
                'grade_note': 'Building grade weighting is unspecified; material grades are descriptive and are not the EI10 disposal indicator.' if self.options.grade_weighting is None else 'Descriptive grade average; not the normative EI10 indicator.'}


def load_reference(path):
    """Read a headed semicolon CSV, XLSX or JSON configuration without zero-fill."""
    path = Path(path)
    if path.suffix.lower() == '.json':
        def unique_pairs(pairs):
            d = {}
            for key, value in pairs:
                if key in d:
                    raise ValueError(f'Duplicate JSON key: {key}')
                d[key] = value
            return d
        result = json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique_pairs)
        return result['data'] if 'data' in result and 'header' in result else result
    if path.suffix.lower() == '.xlsx':
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        rows = iter(wb.active.values)
        header = next(rows)
    elif path.suffix.lower() == '.csv':
        f = path.open(encoding='utf-8-sig', newline='')
        rows = csv.reader(f, delimiter=';')
        header = next(rows)
    else:
        raise ValueError('Reference must be CSV, XLSX or JSON.')
    result = {}
    columns = [str(h or '').strip() for h in header]
    while columns and not columns[-1]: columns.pop()
    if not columns[0] or any(not h for h in columns):
        raise ValueError('Reference table requires a named header for every column.')
    if len({field_name(h) for h in columns}) != len(columns):
        raise ValueError('Duplicate reference columns.')
    try:
        for row in rows:
            if not any(value not in (None, '') for value in row):
                continue
            if len(row) < len(columns) or any(v is not None and str(v).strip() for v in row[len(columns):]):
                raise ValueError('Reference row width differs from header.')
            name = str(row[0] or '').strip()
            if not name or name in result:
                raise ValueError(f'Empty or duplicate reference material: {name}')
            result[name] = canonical_record(dict(zip(columns[1:], row[1:len(columns)])))
    finally:
        if path.suffix.lower() == '.xlsx': wb.close()
        else: f.close()
    return result


def file_hash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()
