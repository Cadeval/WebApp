"""Versioned Vienna municipal unit charges and official contract lookup links.

Amounts were verified against the authority's current pages on 2026-10-03.
Their legal start is 2025-01-01; no legal end date is published. The separate
2026-12-31 snapshot review deadline prevents carrying these amounts into a new
year without verification. A location does not establish service connection,
consumption, assigned bins, or the building's actual bill.
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


PROVIDER_VERSION = 'vienna-utilities-2026.1'
VERIFIED_AS_OF = '2026-10-03'
SNAPSHOT_VALID_UNTIL = '2026-12-31'
EFFECTIVE_FROM = '2025-01-01'
VIENNA = ZoneInfo('Europe/Vienna')
WATER_URL = 'https://www.wien.gv.at/umwelt/eu-trinkwasserrichtlinie'
WATER_METER_URL = 'https://www.wien.gv.at/amtswege/wasserbezugs-wasserzaehlergebuehr-meldung'
WASTE_URL = 'https://www.wien.gv.at/umwelt/hausmuellgebuehren-tarifuebersicht'
NOTICE_URL = 'https://www.wien.gv.at/recht/landesrecht-wien/rechtsvorschriften/pdf/abl/abl2024041s6-8.pdf'
GAS_COMPARISON_URL = 'https://www.e-control.at/tarifkalkulator'
VIENNA_GAS_URL = 'https://www.wienenergie.at/privat/produkte/erdgas/'
VIENNA_HEAT_URL = 'https://www.wienenergie.at/privat/produkte/waerme/fernwaerme/'
AUSTRIA_ENERGY_GUIDANCE_URL = 'https://www.oesterreich.gv.at/de/themen/bauen_und_wohnen/umzug/5/Seite.180309'

_INPUT_LABELS = {
    'municipal_water_connection': 'Municipal water connection',
    'metered_consumption_m3': 'Metered consumption (m³)',
    'water_meter_size': 'Water meter size',
    'municipal_sewer_connection': 'Municipal sewer connection',
    'chargeable_discharge_m3': 'Chargeable discharge (m³)',
    'approved_discharge_adjustments': 'Approved discharge adjustments',
    'assigned_bin_size_litres': 'Bin size (litres)',
    'assigned_bin_count': 'Number of bins',
    'assigned_emptyings_per_year': 'Collections per year',
    'postcode': 'Postcode',
    'annual_consumption_kwh': 'Annual consumption (kWh)',
    'gas_connection': 'Gas connection',
    'current_supplier_and_contract': 'Current supplier and contract',
    'supply_address': 'Supply address',
    'district_heat_connection': 'District heating connection',
    'supplier_and_tariff_model': 'Supplier and tariff model',
    'heat_consumption': 'Heat consumption',
    'metering_and_allocation_method': 'Metering and allocation method',
}

_MUNICIPAL = (
    {'key': 'water', 'name': 'Municipal water consumption', 'price_decimal': '2.27',
     'unit': 'EUR/m³', 'price_basis': 'published_unit_charge',
     'source_url': WATER_URL, 'source_name': 'City of Vienna — Wiener Wasser (MA 31)',
     'additional_sources': [{'name': 'Water meter charges', 'url': WATER_METER_URL}],
     'required_inputs': ['municipal_water_connection', 'metered_consumption_m3', 'water_meter_size'],
     'note': 'Includes 10% VAT. Applies to water supplied by the municipal service if connected. '
             'The separate water meter charge depends on connection size; connection and other charges are not included.'},
    {'key': 'sewer', 'name': 'Municipal sewer discharge', 'price_decimal': '2.49',
     'unit': 'EUR/m³', 'price_basis': 'published_unit_charge',
     'source_url': WATER_URL, 'source_name': 'City of Vienna — water and sewer fees',
     'required_inputs': ['municipal_sewer_connection', 'chargeable_discharge_m3', 'approved_discharge_adjustments'],
     'note': 'Includes 10% VAT. Applies to chargeable discharge into the public sewer if connected. '
             'The chargeable amount and any approved reductions must come from the applicable assessment; no building bill is inferred.'},
    {'key': 'waste_120l_52', 'name': 'Residual waste — 120 L / 52 empties reference',
     'price_decimal': '288.9744', 'unit': 'EUR/bin/year',
     'price_basis': 'published_table_reference', 'bin_litres': 120,
     'bin_count': 1, 'emptyings_per_year': 52,
     'source_url': WASTE_URL, 'source_name': 'City of Vienna — MA 48 residual-waste tariff table',
     'required_inputs': ['assigned_bin_size_litres', 'assigned_bin_count', 'assigned_emptyings_per_year'],
     'note': 'Includes 10% VAT. This is the published annual table entry for one 120-litre residual-waste bin '
             'with 52 emptyings. It is a reference example; the building charge depends on its assigned bins and collection frequency.'},
)


def _now(now):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('Utility lookup requires a timezone-aware current datetime.')
    return now.astimezone(timezone.utc)


def _vienna_authority(jurisdiction):
    return (isinstance(jurisdiction, dict) and jurisdiction.get('known') is True and
            jurisdiction.get('country') == 'AT' and jurisdiction.get('state') == 'Vienna')


def _vienna(jurisdiction):
    # Only the official jurisdiction result can unlock Vienna amounts. A city
    # label, postcode, bounding box or national energy route cannot do this.
    if not _vienna_authority(jurisdiction):
        return False
    districts = jurisdiction.get('districts')
    return (isinstance(districts, list) and bool(districts) and all(
        isinstance(district, dict) and isinstance(district.get('number'), int) and
        not isinstance(district['number'], bool) and 1 <= district['number'] <= 23 and
        district.get('boundary') is False for district in districts))


def _item_metadata():
    return {'currency': 'EUR', 'effective_from': None, 'effective_until': None,
            'verified_as_of': VERIFIED_AS_OF, 'snapshot_valid_until': SNAPSHOT_VALID_UNTIL,
            'current_effective': False}


def _municipal_items(vienna, today):
    items = []
    for template in _MUNICIPAL:
        item = {**_item_metadata(), **copy.deepcopy(template), 'price': None,
                'effective_from': EFFECTIVE_FROM, 'vat_included': True, 'vat_percent': 10,
                'legal_source_url': NOTICE_URL}
        if not vienna:
            item.update(status='unsupported', price_decimal=None,
                note='Vienna municipal charges require a verified interior district location; boundary or missing jurisdiction results do not enable numeric rates. ' + item['note'])
        elif today < date.fromisoformat(EFFECTIVE_FROM):
            item.update(status='not_yet_effective',
                note='This published schedule does not cover dates before 2025-01-01. ' + item['note'])
        elif today > date.fromisoformat(SNAPSHOT_VALID_UNTIL):
            item.update(status='expired',
                note='This snapshot needs re-verification after 2026-12-31; its historical source amount is not a current quote. ' + item['note'])
        else:
            item.update(status='available', price=float(item['price_decimal']), current_effective=True)
        item['note'] += (' Rates were verified on 2026-10-03. The 2026-12-31 review deadline is this application\'s '
                         'snapshot policy, not an expiry published by the authority.')
        items.append(item)
    return items


def _contract_items(austria, vienna):
    gas = {**_item_metadata(), 'key': 'gas', 'name': 'Gas — contract comparison',
        'price': None, 'price_decimal': None, 'unit': None,
        'price_basis': 'contract_quote_required', 'status': 'contract_quote_required' if austria else 'unsupported',
        'source_url': GAS_COMPARISON_URL, 'source_name': 'E-Control — official Austrian gas tariff comparison',
        'required_inputs': ['postcode', 'annual_consumption_kwh', 'gas_connection', 'current_supplier_and_contract'],
        'note': 'Use the official E-Control calculator to compare gas contracts using postcode and annual consumption in kWh. '
                'Energy, network, taxes, fixed charges and offers depend on the selected product; no contract or connection is assumed.'}
    if vienna:
        gas['additional_sources'] = [{'name': 'Wien Energie gas offers', 'url': VIENNA_GAS_URL}]
    heat = {**_item_metadata(), 'key': 'district_heat', 'name': 'District heating — connection and contract',
        'price': None, 'price_decimal': None, 'unit': None,
        'price_basis': 'contract_quote_required', 'status': 'contract_quote_required' if austria else 'unsupported',
        'source_url': VIENNA_HEAT_URL if vienna else AUSTRIA_ENERGY_GUIDANCE_URL,
        'source_name': 'Wien Energie — district heating tariff models' if vienna else 'Austrian federal portal — energy suppliers',
        'required_inputs': ['supply_address', 'district_heat_connection', 'supplier_and_tariff_model',
                            'heat_consumption', 'metering_and_allocation_method'],
        'note': ('Wien Energie publishes several contract-dependent tariff models. Check the model in the contract or annual bill; '
                 'connection, consumption, metering and allocation determine the building charge. No heating contract or tariff is assumed.'
                 if vienna else 'Contact the local heat supplier for its connection and contract terms. The linked official guidance is '
                 'supplier information, not a district-heating tariff comparison or numeric quote.')}
    if not austria:
        for item in (gas, heat):
            item['note'] = 'An Austrian provider route is not verified for this location. ' + item['note']
    return [gas, heat]


def utilities_lookup(*, jurisdiction, now, country=''):
    """Return conditional unit-charge references, never an annual property bill.

    ``jurisdiction`` must be an official known AT/Vienna result for municipal
    numbers. ``country='AT'`` enables national contract links only. Effective
    and review dates use Europe/Vienna, even when the caller supplies UTC.
    No external request, quantity scaling or inferred connection is performed.
    """
    now = _now(now)
    today = now.astimezone(VIENNA).date()
    vienna = _vienna(jurisdiction)
    austria = (country == 'AT' or vienna or
        (isinstance(jurisdiction, dict) and jurisdiction.get('known') is True and jurisdiction.get('country') == 'AT'))
    municipal = _municipal_items(vienna, today)
    status = municipal[0]['status']
    note = ('Municipal unit-charge references apply only if the building uses the relevant Vienna services. '
            'Consumption, connections and waste assignments are required for its actual charges. Gas and heat need contract-specific quotes.')
    if not vienna:
        note = 'Municipal pricing is unsupported outside the officially verified Vienna jurisdiction. ' + note
    elif status == 'expired':
        note = 'The municipal snapshot review deadline has passed; verify the sources before using new amounts. ' + note
    elif status == 'not_yet_effective':
        note = 'This snapshot does not cover the requested historical date. ' + note
    items = municipal + _contract_items(austria, _vienna_authority(jurisdiction))
    for item in items:
        item['required_input_labels'] = [_INPUT_LABELS[key] for key in item['required_inputs']]
    return {'status': status, 'provider_version': PROVIDER_VERSION,
            'verified_as_of': VERIFIED_AS_OF, 'snapshot_valid_until': SNAPSHOT_VALID_UNTIL,
            'lookup_at': now.isoformat().replace('+00:00', 'Z'), 'timezone': 'Europe/Vienna',
            'municipal_jurisdiction': 'Vienna' if vienna else None,
            'items': items, 'note': note}
