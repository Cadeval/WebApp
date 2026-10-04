"""Visible, comparable thesis indicators without a composite design ranking."""
import math

METRICS = [
    ('mass','Installed mass','kg'), ('mass_observation','Material mass over the observation period','kg'),
    ('waste_mass','Initial waste mass','kg'), ('waste_mass_observation','Waste mass over the observation period','kg'),
    ('recyclable_mass','Initial recyclable mass','kg'), ('recyclable_mass_observation','Recyclable mass over the observation period','kg'),
    ('gwp_a1_a3','GWP A1–A3','kg CO₂e'), ('gwp_a1_a3_b4','GWP A1–A3 plus B4','kg CO₂e'),
    ('ap_a1_a3','AP A1–A3','kg SO₂e'), ('ap_a1_a3_b4','AP A1–A3 plus B4','kg SO₂e'),
    ('penrt_a1_a3','PENRT A1–A3','MJ'), ('penrt_a1_a3_b4','PENRT A1–A3 plus B4','MJ'),
    ('global_brutto_price','Global gross material cost','EUR'),
    ('local_brutto_price','Local gross material cost','EUR'),
    ('local_netto_price','Local net material cost','EUR'),
    ('recycling_grade','Descriptive recovery grade','grade 1–5'),
]


def charts(series, *, include_averages=False):
    """Shared zero-axis handles negative GWP and never draws missing data as zero."""
    result=[]
    metrics=list(METRICS)
    if include_averages:
        for indicator,unit in [('gwp','kg CO₂e'),('ap','kg SO₂e'),('penrt','MJ')]:
            for period,label in [('a1_a3','A1–A3'),('a1_a3_b4','A1–A3 plus B4')]:
                metrics.append(('average_'+indicator+'_'+period,'Average '+indicator.upper()+' '+label,unit+' / selected averaging basis'))
    for key,label,unit in metrics:
        values=[(name, totals.get(key)) for name,totals in series]
        finite=[v for _,v in values if isinstance(v,(int,float)) and math.isfinite(v)]
        lower=min([0]+finite);upper=max([0]+finite)
        span=upper-lower or 1
        baseline=140+400*(-lower)/span
        bars=[]
        for name,value in values:
            available=isinstance(value,(int,float)) and math.isfinite(value)
            end=140+400*(value-lower)/span if available else baseline
            bars.append({'label':name,'value':value if available else None,
                         'x':min(end,baseline),'width':abs(end-baseline),
                         'available':available,'display':format(value,'.6g') if available else 'Unavailable'})
        result.append({'key':key,'label':label,'unit':unit,'bars':bars,
                       'baseline':baseline,'height':max(64,40*len(bars)+20)})
    return result


def comparison(documents):
    series=[];warnings=[]; signatures=[]
    for document,report in documents:
        name=document.description or str(document.pk)
        totals=dict(report.get('building',{}))
        totals.update({'average_'+key:value for key,value in report.get('lca_averages',{}).get('values',{}).items()})
        series.append((name,totals))
        provenance=report.get('provenance',{})
        signature=(report.get('options'),provenance.get('configuration_sha256'),report.get('method'))
        signatures.append(signature)
        if report.get('schema_version',0)<2:
            warnings.append(f'{name}: historical report lacks current extraction and averaging diagnostics; recalculate before comparison.')
        if not report.get('complete'):
            warnings.append(f'{name}: assessment coverage is incomplete. Available individual indicators do not establish complete model coverage.')
        if not provenance.get('configuration_sha256') or not report.get('options'):
            warnings.append(f'{name}: method or reference provenance is missing; recalculate before comparison.')
    if signatures and any(s!=signatures[0] for s in signatures[1:]):
        warnings.append('The reference data or assessment boundaries differ. Recalculate with the same dataset, period, exclusions and policies before drawing a comparative conclusion.')
    return {'charts':charts(series,include_averages=True), 'warnings':warnings,
            'comparable':len(series)>=2 and not warnings,
            'series':series}
