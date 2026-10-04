"""Readable, counted diagnostics retaining links to affected IFC products."""
import re
from .element_identity import element_reference, enrich_report

def product_references(model, instance, cache):
    if not hasattr(instance,'id'):return []
    key=instance.id()
    if key in cache:return cache[key]
    queue=[instance];seen=set();products={}
    while queue:
        entity=queue.pop()
        if entity.id() in seen:continue
        seen.add(entity.id())
        if entity.is_a('IfcProduct'):
            guid=getattr(entity,'GlobalId',None)
            if guid:products[entity.id()]=element_reference(entity)
            continue
        # Traverse inverse ownership to products; never expand past a product
        # into containment relations (which would falsely implicate neighbours).
        queue.extend(model.get_inverse(entity))
    cache[key]=[products[key] for key in sorted(products)];return cache[key]

def schema_diagnostics(model, statements):
    cache={};result=[]
    for item in statements:
        instance=item.get('instance')
        result.append({'category':'IFC validation','message':re.sub(r'0x[0-9a-fA-F]+','<address>',str(item.get('message','IFC validation issue'))),
            'attribute':str(item.get('attribute') or ''),
            'instance':str(instance) if instance is not None else '',
            'elements':product_references(model,instance,cache)})
    return result

def grouped_diagnostics(report):
    report=enrich_report(report)
    lookup={e['element_id']:e for e in report.get('inventory',[])}
    lookup.update({row['element_id']:row for row in report.get('rows',[]) if row.get('element_id')})
    def reference(identifier):
        entry=lookup.get(identifier,{'element_id':identifier,'display_name':'Unresolved IFC element'})
        return {key:entry[key] for key in ('element_id','step_id','name','display_name','ifc_class') if key in entry}
    entries=list(report.get('schema_validation',{}).get('diagnostics',[]))
    for category,key in [('Calculation','issues'),('Quantity assumption','quantity_warnings')]:
        for issue in report.get(key,[]):
            entries.append({'category':category,'message':str(issue.get('message','')),'elements':[reference(issue['element_id'])] if issue.get('element_id') else []})
    for row in report.get('rows',[]):
        for message in row.get('issues',[]):
            entries.append({'category':'Material data','message':str(message),'elements':[reference(row['element_id'])] if row.get('element_id') else []})
    groups={}
    for issue in entries:
        base=issue['message'].split('\n\nViolated by:')[0].split('\nOn instance:')[0]
        message=re.sub(r'\s+',' ',base).strip()
        signature=re.sub(r'#\d+','#entity',message)
        signature=re.sub(r"'[0-9A-Za-z_$]{22}'","'IFC identifier'",signature)
        key=(issue['category'],issue.get('attribute',''),signature)
        if key not in groups:
            first=issue['message'].splitlines()[0].strip() or 'IFC validation issue'
            if issue['category']=='IFC validation' and issue.get('attribute'):
                attribute=issue['attribute']
                first=('Geometry has a representation but no required placement' if attribute.endswith('PlacementForShapeRepresentation') else 'IFC requirement not satisfied: '+re.sub(r'(?<=[a-z])(?=[A-Z])',' ',attribute))
            for original,readable in [('Dichte','density'),('Verwertungspotential','recovery grade'),('Nutzungsdauer','service life'),('Abfallreduktion','waste reduction'),('Recycling','recycling percentage')]:
                first=first.replace(original,readable)
            groups[key]={'category':issue['category'],'summary':first[:240],'message':issue['message'],'attribute':issue.get('attribute',''),'count':0,'elements':{}}
        group=groups[key];group['count']+=1
        for element in issue.get('elements',[]):
            group['elements'][element.get('step_id') or element['element_id']]=element
    return [{**g,'elements':list(g['elements'].values())} for g in groups.values()]
