"""Nearest reference-name neighbours for review; never impute IFC coefficients."""
import math
import unicodedata
from collections import Counter

NUMERIC_FIELDS=['Dichte','Verwertungspotential','Abfallreduktion','Recycling','GWP','AP','PENRT','Nutzungsdauer','Globaler Brutto Preis','Lokaler Brutto Preis','Lokaler Netto Preis']

def features(name):
    normalized=''.join(c for c in unicodedata.normalize('NFKD',str(name).casefold()) if not unicodedata.combining(c))
    normalized=' '.join(''.join(c if c.isalnum() else ' ' for c in normalized).split())
    padded=' '+normalized+' '
    return Counter(padded[i:i+3] for i in range(max(0,len(padded)-2)))

def coverage(record):
    missing=[]
    for key in NUMERIC_FIELDS:
        # Reuse the assessment parser so comma decimals and thousands separators
        # have the same meaning in coverage and in numerical calculations.
        from .material_assessment import number
        value=number(record.get(key))
        valid=value is not None
        if valid and key in ('Dichte','Nutzungsdauer'):valid=value>0
        if valid and key=='Verwertungspotential':valid=1<=value<=5
        if valid and key in ('Abfallreduktion','Recycling'):valid=0<=value<=100
        if valid and 'Preis' in key:valid=value>=0
        if not valid:missing.append(key)
    if record.get('Preis Multiplikator') not in ('Volumen','Fläche','Länge'):missing.append('Preis Multiplikator')
    return {'known_fields':len(NUMERIC_FIELDS)+1-len(missing),'required_fields':len(NUMERIC_FIELDS)+1,'missing_fields':missing}

def material_classification(rows,config):
    names=sorted(config);vectors={name:features(name) for name in names}
    df=Counter(feature for vector in vectors.values() for feature in vector)
    idf={feature:math.log((1+len(names))/(1+count))+1 for feature,count in df.items()}
    def weighted(vector):
        result={key:count*idf.get(key,math.log(1+len(names))+1) for key,count in vector.items()}
        norm=math.sqrt(sum(v*v for v in result.values()))
        return {key:value/norm for key,value in result.items()} if norm else {}
    vectors={name:weighted(vector) for name,vector in vectors.items()}
    material_rows={}
    for row in rows:material_rows.setdefault(row['material'],[]).append(row)
    result=[]
    for name,items in sorted(material_rows.items()):
        entry={'material':name,'elements':sorted({row['element_id'] for row in items}),'candidates':[]}
        if name in config:
            entry.update(status='Exact reference match',reference=name,coverage=coverage(config[name]))
        else:
            query=weighted(features(name));ranked=[]
            for candidate,vector in vectors.items():
                score=sum(value*vector.get(key,0) for key,value in query.items())
                if score>=0.2:ranked.append((score,candidate))
            ranked.sort(key=lambda item:(-item[0],item[1]))
            entry.update(status='Unknown — confirmation required',reference=None,coverage=None)
            entry['candidates']=[{'reference':candidate,'similarity':round(score,4),'similarity_percent':round(score*100,1),'coverage':coverage(config[candidate])} for score,candidate in ranked[:3]]
        result.append(entry)
    return {'method':'Character-trigram TF-IDF cosine nearest neighbours of reference names','notice':'Similarity is a name-matching score, not a calibrated probability or proof of physical equivalence. Suggestions never fill coefficients or make unknown material properties known. Confirm composition and the reference before assigning a material.','materials':result}
