"""Routing, provider limits and cross-request cache behaviour."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from unittest import TestCase
from unittest.mock import Mock, patch
import tempfile

from apps.plugins.bim_model_manager.location_lookup import LookupUnavailable, cached_lookup, country_routing, fetch_public_data, lookup_location, _austria_boundary, valid_coordinates


NOW = datetime(2026, 10, 3, 12, 30, tzinfo=timezone.utc)


class LookupTests(TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.cache = Path(self.folder.name)

    def saved(self, loader, *, now=NOW, refresh=False, key='test'):
        return cached_lookup(self.cache, key, loader, now=now, ttl=3600, retry_after=1200,
                             max_stale=86400, refresh=refresh)

    def test_country_routing_uses_geometry_and_refuses_border_or_foreign_location(self):
        for latitude, longitude in [(48.2,16.37), (47.0707,15.4395), (47.8095,13.0550)]:
            self.assertEqual(country_routing(latitude, longitude)['country'], 'AT')
        self.assertEqual(country_routing(48.137,11.575)['status'], 'unsupported')
        geometry = _austria_boundary()[0]
        polygon = geometry.geoms[0] if geometry.geom_type == 'MultiPolygon' else geometry
        longitude, latitude = polygon.exterior.coords[0]
        self.assertEqual(country_routing(latitude, longitude)['status'], 'border')
        for point in [(None,0), (True,0), (float('nan'),16), (91,16), (48,float('inf'))]:
            self.assertFalse(valid_coordinates(*point))
            self.assertEqual(country_routing(*point)['status'], 'missing')

    def test_fresh_cache_and_hard_refresh_cooldown_are_shared_across_requests(self):
        loader = Mock(return_value={'status':'available','value':1})
        first = self.saved(loader)
        self.assertFalse(first['cache']['stale'])
        self.saved(loader, now=NOW+timedelta(minutes=10), refresh=True)
        self.saved(loader, now=NOW+timedelta(minutes=35))
        self.assertEqual(loader.call_count, 1)
        self.saved(loader, now=NOW+timedelta(minutes=36), refresh=True)
        self.assertEqual(loader.call_count, 2)
        self.assertEqual(first['cache']['retry_after_seconds'], 1200)

    def test_provider_outage_uses_labelled_stale_data_then_expires_it(self):
        self.saved(lambda: {'status':'available','value':3})
        fail = Mock(side_effect=LookupUnavailable('Provider offline'))
        stale = self.saved(fail, now=NOW+timedelta(hours=2))
        self.assertEqual(stale['value'], 3)
        self.assertTrue(stale['cache']['stale'])
        self.assertEqual(stale['cache']['notice'], 'Provider offline')
        self.saved(fail, now=NOW+timedelta(hours=2,minutes=5), refresh=True)
        self.assertEqual(fail.call_count, 1)
        expired = self.saved(fail, now=NOW+timedelta(days=2))
        self.assertEqual(expired['status'], 'unavailable')
        self.assertNotIn('value', expired)

    def test_failure_without_saved_values_keeps_provenance_and_limits_retries(self):
        value = {'status':'unavailable','provider':'example','note':'No published interval'}
        loader = Mock(return_value=value)
        result = self.saved(loader)
        self.assertEqual(result['provider'], 'example')
        self.assertIsNone(result['cache']['age_seconds'])
        self.saved(loader, now=NOW+timedelta(seconds=10), refresh=True)
        self.assertEqual(loader.call_count, 1)

    def test_partial_planning_retries_after_cooldown_and_preserves_a_complete_snapshot(self):
        def planning(loader, now, key):
            return cached_lookup(self.cache,key,loader,now=now,ttl=86400,retry_after=300,max_stale=604800)
        partial = {'status':'partial','message':'Plan documents unavailable','zoning':['W3']}
        first = planning(lambda:partial,NOW,'partial-first')
        self.assertEqual(first['status'],'partial')
        recovered = Mock(return_value={'status':'available','zoning':['W3'],'plans':[8091]})
        result = planning(recovered,NOW+timedelta(minutes=6),'partial-first')
        self.assertEqual(recovered.call_count,1)
        self.assertEqual(result['plans'],[8091])
        saved = planning(lambda:{'status':'available','plans':[8091]},NOW,'complete-first')
        failed_refresh = cached_lookup(self.cache,'complete-first',lambda:partial,now=NOW+timedelta(minutes=6),
            ttl=86400,retry_after=300,max_stale=604800,refresh=True)
        self.assertEqual(failed_refresh['plans'],saved['plans'])
        self.assertTrue(failed_refresh['cache']['stale'])
        self.assertEqual(failed_refresh['cache']['notice'],'Plan documents unavailable')

    def test_two_workers_do_not_duplicate_a_slow_lookup(self):
        entered, release = Event(), Event()
        def loader():
            entered.set()
            self.assertTrue(release.wait(3))
            return {'status':'available','value':5}
        with ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(self.saved, loader)
            self.assertTrue(entered.wait(2))
            second_loader = Mock()
            second = self.saved(second_loader)
            self.assertTrue(second['cache']['busy'])
            second_loader.assert_not_called()
            release.set()
            self.assertEqual(first.result(timeout=3)['value'], 5)

    def test_distinct_points_do_not_share_legal_cache_and_return_values_are_isolated(self):
        one = self.saved(lambda: {'status':'available','rows':[1]}, key='48.123456781')
        one['rows'].append(2)
        loader = Mock()
        self.assertEqual(self.saved(loader, key='48.123456781')['rows'], [1])
        loader.assert_not_called()
        second = self.saved(lambda: {'status':'available','rows':[7]}, key='48.123456782')
        self.assertEqual(second['rows'], [7])

    def test_corrupt_cache_recovers_and_unwritable_storage_does_not_bypass_rate_limits(self):
        self.saved(lambda: {'status':'available'})
        next(self.cache.glob('*.json')).write_text('not json')
        loader = Mock(return_value={'status':'available'})
        self.saved(loader)
        self.assertEqual(loader.call_count, 1)
        with patch('apps.plugins.bim_model_manager.location_lookup.os.open', side_effect=OSError('denied')):
            blocked_loader = Mock()
            result = self.saved(blocked_loader, key='blocked')
            blocked_loader.assert_not_called()
            self.assertIn('private lookup cache', result['cache']['notice'])

    def test_austrian_energy_cache_is_global_while_planning_cache_uses_each_exact_point(self):
        with patch('apps.plugins.bim_model_manager.location_energy.energy_lookup', return_value={'status':'available','provider':'awattar_at'}) as energy, \
             patch('apps.plugins.bim_model_manager.location_energy.present_energy', side_effect=lambda saved, **_: saved), \
             patch('apps.plugins.bim_model_manager.location_planning.planning_lookup', return_value={'status':'available','jurisdiction':{'known':True}}) as planning:
            lookup_location(48.2,16.37,self.cache,now=NOW,fetch=Mock())
            lookup_location(48.21,16.38,self.cache,now=NOW,fetch=Mock())
            self.assertEqual(energy.call_count, 1)
            self.assertEqual(planning.call_count, 2)
            lookup_location(48.2,16.37,self.cache,now=NOW,fetch=Mock())
            self.assertEqual(planning.call_count, 2)

    def test_foreign_and_missing_points_do_not_contact_any_provider(self):
        fetch = Mock()
        for point in [(48.137,11.575),(None,None)]:
            result = lookup_location(*point,self.cache,now=NOW,fetch=fetch)
            self.assertEqual(result['energy']['status'], 'unsupported')
            self.assertTrue(all(row['price'] is None for row in result['utilities']['items']))
        fetch.assert_not_called()

    def test_utilities_follow_verified_jurisdiction_while_contract_links_follow_country(self):
        interior = {'known': True, 'country': 'AT', 'state': 'Vienna',
                    'districts': [{'number': 10, 'boundary': False}]}
        with patch('apps.plugins.bim_model_manager.location_energy.energy_lookup', return_value={'status':'available'}), \
             patch('apps.plugins.bim_model_manager.location_energy.present_energy', side_effect=lambda saved, **_: saved), \
             patch('apps.plugins.bim_model_manager.location_planning.planning_lookup', return_value={'status':'available','jurisdiction':interior}):
            result = lookup_location(48.2,16.37,self.cache,now=NOW,fetch=Mock())
        self.assertEqual(result['utilities']['items'][0]['price'],2.27)
        with patch('apps.plugins.bim_model_manager.location_energy.energy_lookup', return_value={'status':'available'}), \
             patch('apps.plugins.bim_model_manager.location_energy.present_energy', side_effect=lambda saved, **_: saved), \
             patch('apps.plugins.bim_model_manager.location_planning.planning_lookup', return_value={'status':'unavailable'}):
            unknown = lookup_location(48.21,16.38,self.cache,now=NOW,fetch=Mock())
        self.assertTrue(all(row['price'] is None for row in unknown['utilities']['items']))
        self.assertEqual(unknown['utilities']['items'][3]['status'],'contract_quote_required')


class TransportTests(TestCase):
    @contextmanager
    def client(self, raw, *, status=200):
        response = Mock(status_code=status)
        response.iter_bytes.return_value = [raw]
        stream = Mock()
        stream.__enter__ = Mock(return_value=response)
        stream.__exit__ = Mock(return_value=False)
        client = Mock()
        client.stream.return_value = stream
        outer = Mock()
        outer.__enter__ = Mock(return_value=client)
        outer.__exit__ = Mock(return_value=False)
        with patch('apps.plugins.bim_model_manager.location_lookup.httpx.Client', return_value=outer) as constructor:
            yield constructor, client

    def test_only_fixed_https_providers_are_reachable_and_redirects_are_rejected(self):
        with self.client(b'{}',status=302) as (constructor, client):
            for url in ['http://127.0.0.1/','https://api.awattar.at/v1/marketdata?url=http://localhost','https://evil.example/']:
                with self.assertRaises(LookupUnavailable):
                    fetch_public_data(url,{})
            constructor.assert_not_called()
            with self.assertRaises(LookupUnavailable):
                fetch_public_data('https://api.awattar.at/v1/marketdata',{})
            self.assertFalse(constructor.call_args.kwargs['follow_redirects'])
            self.assertFalse(constructor.call_args.kwargs['trust_env'])

    def test_json_limits_and_nonfinite_values_are_rejected(self):
        for raw in [b'{"price":NaN}', b'bad json', b' ' * 256001]:
            with self.client(raw), self.assertRaises(LookupUnavailable):
                fetch_public_data('https://api.awattar.at/v1/marketdata',{})
        with self.client(b'{"data":[]}'):
            self.assertEqual(fetch_public_data('https://api.awattar.at/v1/marketdata',{}), {'data':[]})

    def test_plan_xml_remains_bounded_and_entity_free(self):
        for raw in [b'<!DOCTYPE x><x/>', b'<!ENTITY x "bad"><x/>', b' ' * 64001]:
            with self.client(raw), self.assertRaises(LookupUnavailable):
                fetch_public_data('https://data.wien.gv.at/daten/wms',{})
        with self.client(b'<root/>'):
            self.assertEqual(fetch_public_data('https://data.wien.gv.at/daten/wms',{}), b'<root/>')

    def test_small_network_chunks_are_not_buffered_past_the_total_deadline(self):
        with self.client(b'{}') as (_,client):
            response = client.stream.return_value.__enter__.return_value
            response.iter_bytes.return_value = [b'{',b'}']
            with patch('apps.plugins.bim_model_manager.location_lookup.time.monotonic',side_effect=[0,1,9]), self.assertRaises(LookupUnavailable):
                fetch_public_data('https://api.awattar.at/v1/marketdata',{})
            response.iter_bytes.assert_called_once_with()
