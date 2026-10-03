"""Bounded public-data lookups, country routing and private cross-worker caches."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
import copy
import fcntl
import hashlib
import json
import math
import os
import tempfile
import time

import httpx
from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform


class LookupUnavailable(ValueError):
    """A public provider failed or returned data outside the accepted contract."""


ENDPOINTS = {
    'https://api.awattar.at/v1/marketdata': ('json', 256_000),
    'https://data.wien.gv.at/daten/geo': ('json', 2_000_000),
    'https://data.wien.gv.at/daten/wms': ('xml', 64_000),
}


def fetch_public_data(url, params):
    """Only fixed providers are reachable; no redirects, credentials or model data."""
    if url not in ENDPOINTS:
        raise LookupUnavailable('This data provider is not supported.')
    kind, limit = ENDPOINTS[url]
    deadline = time.monotonic() + 6
    try:
        with httpx.Client(timeout=httpx.Timeout(2, connect=2), follow_redirects=False, trust_env=False,
                          headers={'User-Agent': 'Cadevil-location-lookup/0.7', 'Accept-Encoding': 'identity'}) as client:
            with client.stream('GET', url, params=params) as response:
                if response.status_code != 200:
                    raise LookupUnavailable('The public data service is temporarily unavailable.')
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > limit or time.monotonic() > deadline:
                        raise LookupUnavailable('The data response exceeded the lookup limits.')
                    chunks.append(chunk)
        raw = b''.join(chunks)
        if kind == 'xml':
            declaration = raw.upper().replace(b'\x00', b'')
            if b'<!DOCTYPE' in declaration or b'<!ENTITY' in declaration:
                raise LookupUnavailable('The planning service returned unsupported XML.')
            return raw
        return json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    except (httpx.HTTPError, UnicodeError, ValueError, OSError, RecursionError) as error:
        if isinstance(error, LookupUnavailable):
            raise
        raise LookupUnavailable('The public data service could not be read. Try again later.') from error


def valid_coordinates(latitude, longitude):
    return (not isinstance(latitude, bool) and not isinstance(longitude, bool)
            and isinstance(latitude, (int, float)) and isinstance(longitude, (int, float))
            and -90 <= latitude <= 90 and -180 <= longitude <= 180
            and math.isfinite(latitude) and math.isfinite(longitude))


@lru_cache(maxsize=1)
def _austria_boundary():
    data = json.loads((Path(__file__).parent / 'location_data/austria.geojson').read_text())
    geometry = shape(data['geometry'])
    project = Transformer.from_crs('EPSG:4326', 'EPSG:3035', always_xy=True).transform
    return geometry, transform(project, geometry), project


def country_routing(latitude, longitude):
    """Generalized country routing is separate from official planning jurisdiction."""
    if not valid_coordinates(latitude, longitude):
        return {'country': '', 'status': 'missing', 'label': 'Location needed', 'note': 'Set a valid building location to look up local data.'}
    geographic, projected, project = _austria_boundary()
    point = Point(longitude, latitude)
    distance = projected.boundary.distance(transform(project, point))
    if distance <= 1000:
        return {'country': '', 'status': 'border', 'label': 'Country needs confirmation',
                'note': 'This point is close to the generalized Austrian border. Confirm its country before using national prices.'}
    if geographic.covers(point):
        return {'country': 'AT', 'status': 'routed', 'label': 'Austria',
                'note': 'Country selected using Natural Earth for energy-market routing; this is not a legal boundary determination.'}
    return {'country': '', 'status': 'unsupported', 'label': 'Outside Austrian coverage',
            'note': 'Energy and planning providers for this location are not enabled.'}


def _read_cache(path):
    try:
        if path.stat().st_size > 2_500_000:
            return {}
        data = json.loads(path.read_text(), parse_constant=lambda _: None)
        if not isinstance(data, dict) or data.get('schema') != 1:
            return {}
        for key in ('attempted_at', 'fetched_at'):
            if key in data and (not isinstance(data[key], (int, float)) or not math.isfinite(data[key])):
                return {}
        if 'payload' in data and not isinstance(data['payload'], dict):
            return {}
        return data
    except (OSError, ValueError, RecursionError):
        return {}


def _write_cache(path, value):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.lookup-', delete=False) as output:
        temporary = Path(output.name)
        try:
            json.dump(value, output, allow_nan=False, separators=(',', ':'))
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def cached_lookup(cache_root, key, loader, *, now, ttl, retry_after, max_stale, refresh=False):
    """Single-flight across Bolt workers; failures and forced refresh share a cooldown."""
    clock = now.timestamp()
    digest = hashlib.sha256(key.encode()).hexdigest()
    folder = Path(cache_root)
    path = folder / (digest + '.json')
    data = _read_cache(path)

    def present(record, *, busy=False):
        payload = copy.deepcopy(record.get('payload') or record.get('failure_payload') or {'status': 'unavailable', 'message': 'No data is available yet.'})
        age = max(0, clock - record.get('fetched_at', clock)) if record.get('payload') else None
        if age is not None and age > max_stale:
            payload = {'status': 'unavailable', 'message': 'The saved data is too old. The provider could not supply a current result.'}
        stale = bool(record.get('payload') and (age > ttl or record.get('error')))
        cooldown = max(0, int(retry_after - (clock - record.get('attempted_at', 0))))
        payload['cache'] = {'age_seconds': int(age) if age is not None else None, 'stale': stale,
                            'busy': busy, 'retry_after_seconds': cooldown,
                            'retrieved_at': datetime.fromtimestamp(record['fetched_at'], timezone.utc).isoformat() if record.get('fetched_at') else '',
                            'notice': record.get('error', '')}
        return payload

    def reusable(record):
        if not record:
            return False
        since_attempt = clock - record.get('attempted_at', 0)
        if 0 <= since_attempt < retry_after:
            return True
        return bool(not refresh and record.get('payload') and not record.get('error')
                    and 0 <= clock - record.get('fetched_at', 0) < ttl)

    if reusable(data):
        return present(data, busy=not any(data.get(key) for key in ('payload', 'failure_payload', 'error')))
    try:
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(descriptor, 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return present(_read_cache(path) or data, busy=True)
            data = _read_cache(path)
            if reusable(data):
                return present(data)
            # Record the attempt before networking, including failed/crashed requests.
            data.update(schema=1, attempted_at=clock)
            _write_cache(path, data)
            try:
                value = loader()
                if isinstance(value, dict) and value.get('status') in {'unavailable', 'partial'}:
                    data['failure_payload'] = value
                if not isinstance(value, dict) or value.get('status') in {'unavailable', 'partial'}:
                    raise LookupUnavailable(value.get('message', 'The provider returned no current data.') if isinstance(value, dict) else 'The provider returned invalid data.')
                data.update(payload=value, fetched_at=clock, error='')
                data.pop('failure_payload', None)
            except (LookupUnavailable, OSError, RuntimeError, ValueError) as error:
                data['error'] = str(error) if isinstance(error, LookupUnavailable) else 'The data service could not supply a current result. Try again later.'
            _write_cache(path, data)
        return present(data)
    except OSError:
        # Do not bypass shared rate limiting when the private cache cannot be used.
        result = present(data)
        result['cache']['notice'] = 'The private lookup cache is unavailable. Ask an administrator to check its storage.'
        return result


def lookup_location(latitude, longitude, cache_root, *, now=None, refresh=False, fetch=fetch_public_data):
    from .location_energy import energy_lookup, present_energy
    from .location_planning import planning_lookup
    from .location_utilities import utilities_lookup
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Lookup time must include a timezone.')
    country = country_routing(latitude, longitude)
    empty = {'status': 'unsupported', 'message': country['note']}
    if country['country'] != 'AT':
        return {'country': country, 'energy': dict(empty), 'planning': dict(empty),
                'utilities': utilities_lookup(jurisdiction={}, now=now, country=country['country'])}

    def energy():
        saved = cached_lookup(cache_root, 'awattar-at-v1', lambda: energy_lookup(fetch, now=now), now=now,
                              ttl=3600, retry_after=1200, max_stale=86400, refresh=refresh)
        return present_energy(saved, now=now)

    def planning():
        # Full float identity avoids reusing a neighbouring point's legal context.
        key = f'vienna-planning-v1:{latitude!r}:{longitude!r}'
        return cached_lookup(cache_root, key, lambda: planning_lookup(fetch, latitude=latitude, longitude=longitude, now=now),
                             now=now, ttl=86400, retry_after=300, max_stale=604800, refresh=refresh)

    with ThreadPoolExecutor(max_workers=2) as executor:
        energy_future = executor.submit(energy)
        planning_future = executor.submit(planning)
        energy_result = energy_future.result()
        planning_result = planning_future.result()
    return {'country': country, 'energy': energy_result, 'planning': planning_result,
            'utilities': utilities_lookup(jurisdiction=planning_result.get('jurisdiction'),
                                          now=now, country=country['country'])}
