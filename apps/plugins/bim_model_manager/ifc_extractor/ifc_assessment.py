"""IFC adapter sharing the existing quantity resolution with the passport core."""
import hashlib
import json
import logging
import time
from pathlib import Path

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element
import ifcopenshell.util.shape
import ifcopenshell.util.unit
import ifcopenshell.validate

from .element_identity import element_reference, enrich_report
from .diagnostics import schema_diagnostics, grouped_diagnostics
from .material_assessment import AssessmentOptions, MaterialAssessment, file_hash, number
from .parallel_geometry import geometry_measurements
from .quantity_resolution import resolve_element_components, resolve_qto_volume


class InvalidIfc(ValueError):
    def __init__(self, diagnostics):
        self.diagnostics = diagnostics
        super().__init__('IFC schema validation failed; see diagnostics.')


def _assess_ifc(path, config, options=None, *, validate_schema=True, geometry_workers=None):
    model = ifcopenshell.open(str(path))
    schema_issues = []
    validation_diagnostics = []
    if validate_schema:
        logger = ifcopenshell.validate.json_logger()
        ifcopenshell.validate.validate(model, logger, express_rules=True)
        schema_issues = [str(item.get('message', item)) for item in logger.statements]
        validation_diagnostics = schema_diagnostics(model,logger.statements)
    assessment = MaterialAssessment(config, options)
    elements = model.by_type('IfcElement')
    included = [element for element in elements
        if not element.is_a('IfcOpeningElement')
        and not any(element.is_a(cls) for cls in assessment.options.excluded_classes)]
    geometry, processing = geometry_measurements(model, included, threads=geometry_workers)
    scale = ifcopenshell.util.unit.calculate_unit_scale(model)
    visited = set()
    inventory = []
    boxes = []
    model_diagnostics = []
    for element in elements:
        identifier = element.GlobalId
        if identifier in visited:
            assessment.add_issue(identifier, 'Duplicate GlobalId')
            continue
        visited.add(identifier)
        container = ifcopenshell.util.element.get_container(element)
        relationships = list(getattr(element, 'ContainedInStructure', ()) or ())
        storey = container if container and container.is_a('IfcBuildingStorey') else None
        diagnostics = []
        if len(relationships) > 1:
            diagnostics.append('Multiple spatial containment relationships; verify storey assignment')
        if storey is None:
            diagnostics.append('No building-storey containment found; verify assignment')
        entry = {**element_reference(element),
                 'predefined_type':getattr(element, 'PredefinedType', None),
                 'storey_id':storey.GlobalId if storey else None,
                 'storey_name':storey.Name if storey else None,
                 'storey_elevation_m':number(storey.Elevation)*scale if storey and number(storey.Elevation) is not None else None,
                 'diagnostics':diagnostics, 'excluded':False}
        inventory.append(entry)
        if any(element.is_a(cls) for cls in assessment.options.excluded_classes):
            entry['excluded'] = True
            assessment.excluded.append({'element_id':identifier,'ifc_class':element.is_a()})
            continue
        if element.is_a('IfcOpeningElement'):
            entry['excluded'] = True
            assessment.excluded.append({'element_id': identifier, 'ifc_class': element.is_a()})
            continue
        measured = geometry.get(element.id(), {})
        volume, area, length = (measured.get(key) for key in ('volume', 'area', 'length'))
        if measured.get('bounds'):
            lower, upper = measured['bounds']
            boxes.append((identifier, lower, upper))
        qtos = ifcopenshell.util.element.get_psets(element, qtos_only=True)
        scaled_qtos = {name:{key:(number(value)*scale**3 if key in ('GrossVolume','NetVolume') and number(value) is not None else value) for key,value in values.items()} for name,values in qtos.items()}
        total_volume = resolve_qto_volume(scaled_qtos, volume)
        components = resolve_element_components(element, total_volume)
        component_pset = ifcopenshell.util.element.get_pset(element, 'Component Quantities')
        source = 'IFC component quantities' if component_pset and len(component_pset)>1 else 'IFC material allocation'
        if source == 'IFC component quantities' and scale != 1:
            # Explicit component quantities are in the model length unit cubed.
            # Resolve using unscaled total, then convert once, including remainders.
            components = {name:value*scale**3 for name,value in resolve_element_components(element, total_volume/scale**3).items()}
        for field in ('NetSideArea','GrossSideArea','NetArea','GrossArea'):
            found = next((number(v[field]) for v in qtos.values() if field in v and number(v[field]) is not None and number(v[field])>=0),None)
            if found is not None:
                area = found * scale**2
                break
        for values in qtos.values():
            if number(values.get('Length')) is not None:
                length = number(values['Length'])*scale
                break
        if total_volume <= 0 and not any(value > 0 for value in components.values()):
            assessment.add_issue(identifier, 'No positive material volume from geometry or exported quantities')
        if source == 'IFC material allocation':
            assessment.quantity_warnings.append({'element_id':identifier, 'message':'Material volumes allocated from overall element volume; verify proportions.'})
        if len(components) > 1:
            assessment.quantity_warnings.append({'element_id':identifier, 'message':'Element area and length are shared across components; verify material-specific price quantities.'})
        if total_volume>0 and abs(sum(components.values())-total_volume)>max(1e-8,total_volume*0.001):
            assessment.add_issue(identifier,'Component volumes do not reconcile with element volume')
        entry.update(volume_m3=total_volume if total_volume>0 or volume is not None or qtos else None,
                     area_m2=area, length_m=length, materials=sorted(components),
                     quantity_source=source)
        assessment.add_element(identifier, element.is_a(), components, area=area, length=length, quantity_source=source)
    report = assessment.report()
    report['schema_validation'] = {'checked':validate_schema,'valid':not schema_issues if validate_schema else None,'occurrences':len(validation_diagnostics),'diagnostics':validation_diagnostics}
    report['calculation_complete'] = report['complete']
    if schema_issues:report['complete'] = False
    report['inventory'] = inventory
    report['geometry_processing'] = {'engine': 'IfcOpenShell multicore iterator', **processing}
    # Broad-phase candidates only: AABB intersection does not prove a solid clash.
    # Touching faces are not overlaps. A sweep avoids testing separated x-ranges.
    active = []
    truncated = False
    for identifier, lower, upper in sorted(boxes, key=lambda box:box[1][0]):
        active = [box for box in active if box[2][0] > lower[0]+1e-6]
        for other, lo, hi in active:
            if all(min(upper[axis],hi[axis])-max(lower[axis],lo[axis])>1e-6 for axis in range(3)):
                model_diagnostics.append({'kind':'potential_overlap', 'element_ids':[other,identifier],
                    'message':'World-coordinate bounding boxes overlap; inspect geometry and intended connections. This is not a confirmed solid intersection.'})
                if len(model_diagnostics)>=200:
                    truncated=True
                    break
        if truncated: break
        active.append((identifier,lower,upper))
    report['model_diagnostics'] = model_diagnostics
    report['overlap_check'] = {'method':'positive-volume world-coordinate bounding-box candidates',
                              'candidate_limit':200, 'truncated':truncated,
                              'geometry_elements_checked':len(boxes)}
    report['provenance'] = {'ifc_sha256':file_hash(path), 'ifc_schema':model.schema,
                            'configuration_sha256':hashlib.sha256(json.dumps(config,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest(),
                            'configuration':config,
                            'assessment_code_sha256':file_hash(Path(__file__).with_name('material_assessment.py')),
                            'code_files_sha256':{name:file_hash(Path(__file__).with_name(name)) for name in
                                ('material_assessment.py','ifc_assessment.py','quantity_resolution.py','parallel_geometry.py','recovery_costs.py','diagnostics.py','material_neighbors.py','assessment_futures.py','element_identity.py')}}
    report = enrich_report(report)
    report['diagnostic_groups'] = grouped_diagnostics(report)
    return report


def assess_ifc(path, config, options=None, *, validate_schema=True, geometry_workers=None, parallel_validation=None):
    from .assessment_futures import should_parallelize, parallel_assessment
    logger = logging.getLogger('cadevil.assessment')
    started = time.perf_counter()
    logger.info('Assessment started', extra={'event': 'assessment_started', 'operation': 'assess_ifc'})
    try:
        if validate_schema and should_parallelize(path,parallel_validation):
            report = parallel_assessment(_assess_ifc,path,config,options,geometry_workers)
        else:
            report = _assess_ifc(path,config,options,validate_schema=validate_schema,geometry_workers=geometry_workers)
    except Exception as error:
        extra = {'event': 'assessment_failed', 'operation': 'assess_ifc',
                 'error_type': type(error).__name__, 'outcome': 'failed',
                 'duration_ms': round((time.perf_counter() - started) * 1000, 2)}
        logger.log(logging.WARNING if isinstance(error, (ValueError, OSError)) else logging.ERROR,
                   'Assessment failed; details remain in the assessment response', extra=extra,
                   exc_info=not isinstance(error, (ValueError, OSError)))
        raise
    logger.info('Assessment completed', extra={
        'event': 'assessment_completed', 'operation': 'assess_ifc', 'outcome': 'completed',
        'duration_ms': round((time.perf_counter() - started) * 1000, 2),
        'complete': bool(report.get('complete')), 'issue_count': len(report.get('issues', [])),
        'material_count': len(report.get('materials', {})),
    })
    return report


def main():
    import argparse
    from .material_assessment import load_reference
    parser = argparse.ArgumentParser(description='Generate the thesis material passport from IFC and the MP reference workbook.')
    parser.add_argument('ifc')
    parser.add_argument('reference')
    parser.add_argument('output')
    parser.add_argument('--years', type=int, default=50)
    parser.add_argument('--include-endpoint', action='store_true')
    parser.add_argument('--grade-weighting', choices=['mass','equal'], default='mass')
    parser.add_argument('--lca-averaging', choices=['installed_mass','material_mean'])
    args = parser.parse_args()
    try:
        report = assess_ifc(args.ifc,load_reference(args.reference),AssessmentOptions(years=args.years,include_endpoint=args.include_endpoint,grade_weighting=args.grade_weighting,lca_averaging=args.lca_averaging))
        report['provenance']['reference_file_sha256'] = file_hash(args.reference)
        report['provenance']['reference_filename'] = Path(args.reference).name
    except InvalidIfc as exc:
        report = {'complete':False,'schema_issues':exc.diagnostics}
    Path(args.output).write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')


if __name__ == '__main__': main()
