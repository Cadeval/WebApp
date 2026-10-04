"""Readable IFC labels with stable identifiers retained separately."""
from collections import Counter
import re

def clean(value):
    return ' '.join(str(value or '').split())

def readable_tag(value):
    text=clean(value)
    if re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}',text) or re.fullmatch(r'[0-3][0-9A-Za-z_$]{21}',text):return ''
    return text

def element_reference(element):
    cls=element.is_a()
    name=clean(getattr(element,'Name',None))
    tag=clean(getattr(element,'Tag',None))
    long_name=clean(getattr(element,'LongName',None))
    visible_tag=readable_tag(tag)
    label=name or long_name or (cls+' '+visible_tag if visible_tag else cls+' (unnamed)')
    import ifcopenshell.util.element
    container=ifcopenshell.util.element.get_container(element)
    storey_name=clean(getattr(container,'Name',None)) if container and container.is_a('IfcBuildingStorey') else None
    return {'storey_name':storey_name,'element_id':getattr(element,'GlobalId',None), 'step_id':element.id(),
            'name':name or None,'long_name':long_name or None,'tag':tag or None,
            'ifc_class':cls,'display_name':label}

def label_references(entries):
    def base(e):
        cls=clean(e.get('ifc_class'))
        tag=readable_tag(e.get('tag'))
        return clean(e.get('name') or e.get('long_name')) or ((cls+' '+tag) if cls and tag else cls+' (unnamed)' if cls else clean(e.get('display_name')) or 'Unnamed element')
    counts=Counter((e.get('ifc_class'),base(e)) for e in entries)
    for position,e in enumerate(entries,1):
        label=base(e)
        if counts[(e.get('ifc_class'),label)]>1:
            context=readable_tag(e.get('tag')) or clean(e.get('storey_name') or e.get('ifc_class'))
            if context and context not in label:label+=' · '+context
            identity=e.get('step_id')
            label+=f' (#{identity})' if identity else f' (instance {position})'
        e['display_name']=label
    return entries

def enrich_report(report):
    import copy
    result=copy.deepcopy(report)
    keys=('element_id','step_id','name','long_name','tag','ifc_class','display_name','storey_name')
    identities={}
    # Older reports may carry names on rows without a separate inventory.
    sources=list(result.get('rows',[]))+[{**e,'element_id':identifier} for identifier,e in result.get('elements',{}).items()]+list(result.get('inventory',[]))
    for diagnostic in result.get('schema_validation',{}).get('diagnostics',[]):sources.extend(diagnostic.get('elements',[]))
    for e in sources:
        identifier=e.get('element_id')
        if not identifier:continue
        identity=identities.setdefault(identifier,{'element_id':identifier})
        for key in keys:
            if e.get(key) is not None and clean(e.get(key)):identity[key]=e[key]
    label_references(list(identities.values()))
    def ref(identifier):
        return dict(identities.get(identifier,{'element_id':identifier,'display_name':'Unresolved IFC element'}))
    for e in result.get('inventory',[]):e.update(ref(e.get('element_id')))
    for row in result.get('rows',[]):row.update(ref(row.get('element_id')))
    for identifier,e in result.get('elements',{}).items():e.update(ref(identifier))
    for diagnostic in result.get('model_diagnostics',[]):diagnostic['elements']=[ref(i) for i in diagnostic.get('element_ids',[])]
    for material in result.get('material_classification',{}).get('materials',[]):material['element_references']=[ref(i) for i in material.get('elements',[])]
    for diagnostic in result.get('schema_validation',{}).get('diagnostics',[]):
        diagnostic['elements']=[ref(e.get('element_id')) for e in diagnostic.get('elements',[])]
    return result
