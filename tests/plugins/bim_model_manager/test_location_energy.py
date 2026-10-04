"""Deterministic tests of official interval parsing, units and cached clocks."""
import copy
import math
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from plugins.bim_model_manager.location_energy import AWATTAR_URL, energy_lookup, present_energy


UTC = timezone.utc
NOW = datetime(2026, 10, 3, 12, 30, tzinfo=UTC)


def row(start, price=42.09, minutes=60, unit='Eur/MWh'):
    return {'start_timestamp': int(start.timestamp() * 1000),
            'end_timestamp': int((start + timedelta(minutes=minutes)).timestamp() * 1000),
            'marketprice': price, 'unit': unit}


def feed(*rows):
    return {'object': 'list', 'data': list(rows)}


class LocationEnergyTests(unittest.TestCase):
    def lookup(self, payload, now=NOW):
        fetch = Mock(return_value=payload)
        result = energy_lookup(fetch, now=now)
        return result, fetch

    def test_documented_request_and_exact_mwh_to_kwh_conversion(self):
        result, fetch = self.lookup(feed(row(NOW.replace(minute=0))))
        fetch.assert_called_once_with(AWATTAR_URL, {'start': 1791028800000, 'end': 1791115200000})
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['current']['price_eur_mwh'], 42.09)
        self.assertEqual(result['current']['price_eur_kwh'], .04209)
        self.assertEqual(result['units'], {'price': 'EUR/kWh', 'source_price': 'EUR/MWh'})
        self.assertEqual(result['current']['start'], '2026-10-03T12:00:00Z')
        self.assertEqual(result['current']['start_local'], '2026-10-03T14:00:00+02:00')
        self.assertEqual(result['market'], 'AT')
        self.assertEqual(result['price_basis'], 'wholesale_day_ahead')
        self.assertNotIn('price', result['retail'])
        self.assertIn('network charges', result['note'])
        self.assertIn('annual_consumption_kwh', result['retail']['required_inputs'])

    def test_negative_zero_and_fractional_quarter_hour_prices_remain_valid(self):
        start = NOW.replace(minute=0)
        result, _ = self.lookup(feed(row(start, -23.45, 15), row(start + timedelta(minutes=15), 0, 15),
            row(start + timedelta(minutes=30), 1.25, 15.5)))
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['intervals'][0]['price_eur_kwh'], -.02345)
        self.assertEqual(result['intervals'][1]['price_eur_kwh'], 0)
        self.assertEqual(result['current']['duration_minutes'], 15.5)
        self.assertEqual(result['current']['price_eur_kwh'], .00125)

    def test_current_selection_changes_at_boundary_without_changing_cached_snapshot(self):
        start = NOW.replace(minute=0)
        snapshot, _ = self.lookup(feed(row(start, 10), row(start + timedelta(hours=1), 20)))
        original = copy.deepcopy(snapshot)
        result = present_energy(snapshot, now=start + timedelta(hours=1))
        self.assertEqual(result['current']['price_eur_kwh'], .020)
        self.assertEqual(result['retrieved_at'], snapshot['retrieved_at'])
        self.assertEqual(snapshot, original)
        result['intervals'][0]['price_eur_mwh'] = 999
        self.assertEqual(snapshot, original)

    def test_expired_and_future_snapshot_has_no_invented_current_price(self):
        start = NOW.replace(minute=0)
        snapshot, _ = self.lookup(feed(row(start)))
        expired = present_energy(snapshot, now=start + timedelta(hours=1))
        future = present_energy(snapshot, now=start - timedelta(seconds=1))
        for result in (expired, future):
            self.assertIsNone(result['current'])
            self.assertFalse(result['coverage']['current_available'])
            self.assertIn('No published interval covers', result['note'])

    def test_dst_autumn_repeated_hour_preserves_distinct_offsets_and_utc_intervals(self):
        first = datetime(2026, 10, 25, 0, tzinfo=UTC)
        result, _ = self.lookup(feed(row(first, 10), row(first + timedelta(hours=1), 20)), now=first + timedelta(minutes=90))
        self.assertEqual(result['intervals'][0]['start_local'], '2026-10-25T02:00:00+02:00')
        self.assertEqual(result['intervals'][1]['start_local'], '2026-10-25T02:00:00+01:00')
        self.assertEqual(result['current']['price_eur_kwh'], .020)
        self.assertEqual(result['coverage']['gap_count'], 0)

    def test_dst_spring_missing_clock_hour_has_no_utc_gap(self):
        first = datetime(2026, 3, 29, 0, tzinfo=UTC)
        result, _ = self.lookup(feed(row(first), row(first + timedelta(hours=1))), now=first)
        self.assertEqual(result['intervals'][0]['start_local'], '2026-03-29T01:00:00+01:00')
        self.assertEqual(result['intervals'][1]['start_local'], '2026-03-29T03:00:00+02:00')
        self.assertEqual(result['coverage']['gap_count'], 0)

    def test_unsorted_identical_duplicates_deduplicate_without_losing_price(self):
        start = NOW.replace(minute=0)
        result, _ = self.lookup(feed(row(start + timedelta(hours=1), 20), row(start, 10), row(start, 10)))
        self.assertEqual(result['coverage']['interval_count'], 2)
        self.assertEqual(result['current']['price_eur_kwh'], .010)

    def test_conflicting_duplicates_and_overlaps_fail_without_partial_prices(self):
        start = NOW.replace(minute=0)
        for payload in (feed(row(start, 10), row(start, 20)),
                        feed(row(start), row(start + timedelta(minutes=30)))):
            result, _ = self.lookup(payload)
            self.assertEqual(result['status'], 'unavailable')
            self.assertEqual(result['intervals'], [])
            self.assertIsNone(result['current'])

    def test_missing_coverage_gap_is_visible(self):
        start = NOW.replace(minute=0)
        result, _ = self.lookup(feed(row(start, minutes=15), row(start + timedelta(minutes=45), minutes=15)))
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['coverage']['gap_count'], 1)
        self.assertIsNone(result['current'])
        self.assertIn('gaps', result['note'])

    def test_empty_feed_and_transport_failure_are_nonfatal(self):
        result, _ = self.lookup(feed())
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('No prices have been published', result['note'])
        result = energy_lookup(Mock(side_effect=TimeoutError('private request details')), now=NOW)
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('provider could not be reached', result['note'])
        self.assertNotIn('private request', result['note'])

    def test_unknown_units_nonfinite_and_wrong_types_are_rejected(self):
        start = NOW.replace(minute=0)
        cases = [row(start, unit='EUR/kWh'), row(start, unit='USD/MWh'), row(start, math.inf),
                 row(start, math.nan), row(start, True), row(start, '42.09'), row(start, 10 ** 1000),
                 {'start_timestamp': True, 'end_timestamp': 1000, 'marketprice': 1, 'unit': 'Eur/MWh'}]
        for invalid in cases:
            with self.subTest(invalid=str(invalid)[:100]):
                result, _ = self.lookup(feed(invalid))
                self.assertEqual(result['status'], 'unavailable')
                self.assertEqual(result['intervals'], [])

    def test_empty_reversed_and_unbounded_intervals_are_rejected(self):
        start = NOW.replace(minute=0)
        for payload in (feed(row(start, minutes=0)), feed(row(start, minutes=-1)),
                        feed(row(start, minutes=48 * 60 + .001)),
                        feed(row(start), row(start + timedelta(hours=48))),
                        feed(*[row(start + timedelta(minutes=15 * i), minutes=15) for i in range(193)])):
            result, _ = self.lookup(payload)
            self.assertEqual(result['status'], 'unavailable')
            self.assertEqual(result['intervals'], [])

    def test_exact_48_hour_192_quarter_hour_limit_is_supported(self):
        start = NOW.replace(minute=0)
        result, _ = self.lookup(feed(*[row(start + timedelta(minutes=15 * i), minutes=15) for i in range(192)]))
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['coverage']['interval_count'], 192)

    def test_cached_snapshot_is_revalidated_and_conversion_recomputed(self):
        snapshot, _ = self.lookup(feed(row(NOW.replace(minute=0))))
        snapshot['intervals'][0]['price_eur_kwh'] = 999
        self.assertEqual(present_energy(snapshot, now=NOW)['current']['price_eur_kwh'], .04209)
        snapshot['intervals'][0]['price_eur_mwh'] = math.nan
        self.assertEqual(present_energy(snapshot, now=NOW)['status'], 'unavailable')
        snapshot['retrieved_at'] = 'yesterday'
        self.assertEqual(present_energy(snapshot, now=NOW)['status'], 'unavailable')

    def test_cache_provenance_is_preserved_on_all_presentation_returns(self):
        available, _ = self.lookup(feed(row(NOW.replace(minute=0))))
        unavailable = energy_lookup(Mock(side_effect=TimeoutError()), now=NOW)
        invalid_provider = {'status': 'unavailable', 'message': 'The provider is temporarily unavailable; retry later.'}
        invalid_timestamp = copy.deepcopy(available); invalid_timestamp['retrieved_at'] = 'invalid'
        invalid_intervals = copy.deepcopy(available); invalid_intervals['intervals'] = [None]
        for snapshot in (available, unavailable, invalid_provider, invalid_timestamp, invalid_intervals):
            snapshot['cache'] = {'age_seconds': 120, 'stale': True, 'busy': False,
                'retry_after_seconds': 30, 'retrieved_at': '2026-10-03T12:28:00Z',
                'notice': 'Showing the last successful response.'}
            with self.subTest(status=snapshot.get('status')):
                result = present_energy(snapshot, now=NOW)
                self.assertEqual(result['cache'], snapshot['cache'])
                result['cache']['notice'] = 'Changed display copy'
                self.assertNotEqual(result['cache']['notice'], snapshot['cache']['notice'])
        result = present_energy(invalid_provider, now=NOW)
        self.assertIn('retry later', result['note'])
        self.assertEqual(result['provider'], 'awattar_at')
        self.assertEqual(result['retail']['provider'], 'E-Control')
        self.assertIn('retrieval timestamp', present_energy(invalid_timestamp, now=NOW)['note'])
        self.assertIn('intervals are invalid', present_energy(invalid_intervals, now=NOW)['note'])

    def test_naive_current_time_is_rejected_before_fetch(self):
        fetch = Mock()
        with self.assertRaisesRegex(ValueError, 'timezone-aware'):
            energy_lookup(fetch, now=NOW.replace(tzinfo=None))
        fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
