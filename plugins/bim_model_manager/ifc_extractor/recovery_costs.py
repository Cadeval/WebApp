import math

def recovery_cost_analysis(rows):
    groups={str(i):{'grade':i,'known_cost_eur':0.0,'priced_rows':0,'missing_price_rows':0,'rows':0} for i in range(1,6)}
    groups['ungraded']={'grade':None,'known_cost_eur':0.0,'priced_rows':0,'missing_price_rows':0,'rows':0}
    for row in rows:
        grade=row.get('recycling_grade')
        key=str(int(grade)) if isinstance(grade,(int,float)) and math.isfinite(grade) and grade in range(1,6) else 'ungraded'
        group=groups[key];group['rows']+=1
        price=row.get('global_brutto_price')
        if isinstance(price,(int,float)) and math.isfinite(price) and price>=0:
            group['known_cost_eur']+=price;group['priced_rows']+=1
        else:group['missing_price_rows']+=1
    subtotal=sum(g['known_cost_eur'] for g in groups.values())
    for group in groups.values():
        group['cost_eur']=group['known_cost_eur'] if not group['missing_price_rows'] else None
        group['share_of_known_cost_percent']=100*group['known_cost_eur']/subtotal if subtotal>0 else None
    return {'currency':'EUR','cost_basis':'Initial global gross material cost','method':'Sum material-row purchase costs by their descriptive recovery grade; no assumed resale or disposal value.','known_cost_eur':subtotal,'groups':list(groups.values())}
