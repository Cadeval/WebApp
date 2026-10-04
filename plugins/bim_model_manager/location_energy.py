"""Official Austrian day-ahead wholesale prices, never a retail tariff quote.

The caller supplies bounded, allowlisted JSON transport and shared caching.
No building coordinates or postcode are sent to the country-wide price feed.
"""
from __future__ import annotations

import copy
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo


AWATTAR_URL = 'https://api.awattar.at/v1/marketdata'
AWATTAR_DOCS = 'https://www.awattar.at/services/api'
ECONTROL_TARIFF_URL = 'https://www.e-control.at/tarifkalkulator'
MAX_INTERVALS = 192
MAX_WINDOW = timedelta(hours=48)
VIENNA = ZoneInfo('Europe/Vienna')
WHOLESALE_NOTE = ('EPEX Spot day-ahead wholesale prices for Austria. These exclude '
    'supplier charges, network charges, taxes and VAT, and are not a household retail tariff.')


class EnergyDataError(ValueError):
    """Provider data cannot be interpreted reliably."""


def _utc(now):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('Energy lookup requires a timezone-aware current datetime.')
    return now.astimezone(timezone.utc)


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _millisecond_date(value):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise EnergyDataError('The wholesale feed contains an invalid interval timestamp.')
    try:
        return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=value)
    except (OverflowError, ValueError):
        raise EnergyDataError('The wholesale feed contains an out-of-range interval timestamp.') from None


def _number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise EnergyDataError('The wholesale feed contains an invalid price.')
    try:
        price = float(value)
    except (OverflowError, ValueError):
        raise EnergyDataError('The wholesale feed contains an unusable price.') from None
    if not math.isfinite(price):
        raise EnergyDataError('The wholesale feed contains a non-finite price.')
    return price


def _parse_intervals(payload):
    if not isinstance(payload, dict) or payload.get('object') != 'list' or not isinstance(payload.get('data'), list):
        raise EnergyDataError('The wholesale feed did not contain the documented interval list.')
    rows = payload['data']
    if len(rows) > MAX_INTERVALS:
        raise EnergyDataError('The wholesale feed exceeds the 192-interval limit.')
    intervals = {}
    for row in rows:
        if not isinstance(row, dict):
            raise EnergyDataError('The wholesale feed contains an invalid interval.')
        unit = row.get('unit')
        if not isinstance(unit, str) or unit.strip().casefold() != 'eur/mwh':
            raise EnergyDataError('The wholesale feed uses an unsupported price unit; EUR/MWh is required.')
        start = _millisecond_date(row.get('start_timestamp'))
        end = _millisecond_date(row.get('end_timestamp'))
        if end <= start:
            raise EnergyDataError('The wholesale feed contains an empty or reversed interval.')
        price = _number(row.get('marketprice'))
        key = (row['start_timestamp'], row['end_timestamp'])
        if key in intervals:
            if intervals[key]['price_eur_mwh'] != price:
                raise EnergyDataError('The wholesale feed contains conflicting prices for the same interval.')
            continue
        intervals[key] = {
            'start_timestamp': key[0], 'end_timestamp': key[1],
            'start': _iso(start), 'end': _iso(end),
            'start_local': start.astimezone(VIENNA).isoformat(),
            'end_local': end.astimezone(VIENNA).isoformat(),
            'duration_minutes': (end - start).total_seconds() / 60,
            'price_eur_mwh': price,
            'price_eur_kwh': float(Decimal(str(price)) / Decimal(1000)),
        }
    ordered = sorted(intervals.values(), key=lambda row: row['start_timestamp'])
    if ordered:
        span = _millisecond_date(ordered[-1]['end_timestamp']) - _millisecond_date(ordered[0]['start_timestamp'])
        if span > MAX_WINDOW:
            raise EnergyDataError('The wholesale feed exceeds the 48-hour window limit.')
        if any(left['end_timestamp'] > right['start_timestamp'] for left, right in zip(ordered, ordered[1:])):
            raise EnergyDataError('The wholesale feed contains overlapping price intervals.')
    return ordered


def _base(now):
    return {
        'status': 'unavailable', 'provider': 'awattar_at',
        'provider_name': 'aWATTar Austria / EPEX Spot',
        'source_url': AWATTAR_URL, 'documentation_url': AWATTAR_DOCS,
        'retrieved_at': _iso(now), 'timezone': 'Europe/Vienna',
        'market': 'AT', 'market_scope': 'Austria electricity bidding area; country-wide wholesale prices',
        'price_basis': 'wholesale_day_ahead',
        'units': {'price': 'EUR/kWh', 'source_price': 'EUR/MWh'},
        'intervals': [], 'current': None,
        'coverage': {'start': None, 'end': None, 'interval_count': 0,
                     'gap_count': 0, 'current_available': False},
        'note': WHOLESALE_NOTE,
        'retail': {
            'status': 'external_quote_required', 'provider': 'E-Control',
            'source_url': ECONTROL_TARIFF_URL,
            'required_inputs': ['postcode', 'annual_consumption_kwh'],
            'note': 'Compare full household tariffs in the official E-Control calculator using the building postcode and annual consumption.',
        },
    }


def _present(result, now):
    intervals = result['intervals']
    now_ms = (now - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds() * 1000
    current = next((row for row in intervals if row['start_timestamp'] <= now_ms < row['end_timestamp']), None)
    gaps = sum(left['end_timestamp'] < right['start_timestamp'] for left, right in zip(intervals, intervals[1:]))
    result.update(status='available' if intervals else 'unavailable', current=copy.deepcopy(current),
        coverage={'start': intervals[0]['start'] if intervals else None,
                  'end': intervals[-1]['end'] if intervals else None,
                  'interval_count': len(intervals), 'gap_count': gaps,
                  'current_available': current is not None},
        note=WHOLESALE_NOTE)
    if not intervals:
        result['note'] += ' No prices have been published for the requested window.'
    elif current is None:
        result['note'] += ' No published interval covers the requested current time; see the dated intervals.'
    if gaps:
        result['note'] += ' There are gaps in the published interval coverage.'
    return result


def energy_lookup(fetch_json, *, now):
    """Fetch a bounded 24-hour forecast window using the documented public API.

    `now` must be aware; output timestamps are UTC, with Vienna offset-aware
    labels alongside them. Transport exceptions become an unavailable result.
    The caller must cache attempts globally to honour the provider's fair use.
    """
    now = _utc(now)
    result = _base(now)
    start = now.replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=24)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    params = {'start': int((start - epoch).total_seconds() * 1000),
              'end': int((end - epoch).total_seconds() * 1000)}
    result['request'] = params.copy()
    try:
        payload = fetch_json(AWATTAR_URL, params)
    except Exception:
        result['note'] += ' The provider could not be reached; wholesale prices are unavailable.'
        return result
    try:
        result['intervals'] = _parse_intervals(payload)
    except EnergyDataError as error:
        result['note'] += ' ' + str(error)
        return result
    return _present(result, now)


def present_energy(snapshot, *, now):
    """Reselect the current price from a cached snapshot without fetching again.

    Retrieval time stays attached to the cached data. This function returns a
    fresh dictionary, so presenting it never mutates the shared cache.
    """
    now = _utc(now)
    result = _base(now)
    if isinstance(snapshot, dict) and isinstance(snapshot.get('cache'), dict):
        result['cache'] = copy.deepcopy(snapshot['cache'])
    def unavailable_note(fallback):
        if isinstance(snapshot, dict):
            message = snapshot.get('message')
            if not message and snapshot.get('status') != 'available':
                message = snapshot.get('note')
            if isinstance(message, str) and message.strip():
                return (message if message.startswith(WHOLESALE_NOTE) else WHOLESALE_NOTE + ' ' + message)[:1000]
        return WHOLESALE_NOTE + ' ' + fallback
    if not isinstance(snapshot, dict) or snapshot.get('provider') != 'awattar_at':
        result['note'] = unavailable_note('The cached wholesale data is invalid.')
        return result
    retrieved_at = snapshot.get('retrieved_at')
    try:
        retrieved = datetime.fromisoformat(retrieved_at.replace('Z', '+00:00'))
        result['retrieved_at'] = _iso(_utc(retrieved))
    except (AttributeError, TypeError, ValueError):
        result['note'] = unavailable_note('The cached wholesale data has no valid retrieval timestamp.')
        return result
    if isinstance(snapshot.get('request'), dict):
        result['request'] = copy.deepcopy(snapshot['request'])
    if snapshot.get('status') != 'available':
        result['note'] = unavailable_note('Wholesale prices are unavailable.')
        return result
    try:
        rows = snapshot['intervals']
        if not isinstance(rows, list):
            raise EnergyDataError('The cached wholesale interval list is invalid.')
        payload = {'object': 'list', 'data': [
            {'start_timestamp': row['start_timestamp'], 'end_timestamp': row['end_timestamp'],
             'marketprice': row['price_eur_mwh'], 'unit': 'Eur/MWh'} for row in rows]}
        result['intervals'] = _parse_intervals(payload)
    except (EnergyDataError, KeyError, TypeError):
        result['note'] = unavailable_note('The cached wholesale intervals are invalid.')
        return result
    return _present(result, now)
