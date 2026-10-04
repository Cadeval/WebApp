"""Version, geography, billing units and date boundaries of utility references."""
import json
import unittest
from datetime import datetime, timedelta, timezone

from plugins.bim_model_manager.location_utilities import utilities_lookup


UTC = timezone.utc
NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
VIENNA = {'known': True, 'country': 'AT', 'state': 'Vienna', 'districts': [{'number': 1, 'boundary': False}]}


class LocationUtilitiesTests(unittest.TestCase):
    def lookup(self, jurisdiction=VIENNA, now=NOW, country=''):
        return utilities_lookup(jurisdiction=jurisdiction, now=now, country=country)

    def test_exact_verified_vienna_rates_vat_and_real_legal_start(self):
        result = self.lookup()
        self.assertEqual(result['status'], 'available')
        rates = {row['key']: row for row in result['items']}
        self.assertEqual(rates['water']['price'], 2.27)
        self.assertEqual(rates['sewer']['price'], 2.49)
        self.assertEqual(rates['water']['unit'], 'EUR/m³')
        self.assertEqual(rates['sewer']['unit'], 'EUR/m³')
        for key in ('water', 'sewer', 'waste_120l_52'):
            row = rates[key]
            self.assertTrue(row['vat_included'])
            self.assertEqual(row['vat_percent'], 10)
            self.assertEqual(row['effective_from'], '2025-01-01')
            self.assertIsNone(row['effective_until'])
            self.assertEqual(row['verified_as_of'], '2026-10-03')
            self.assertEqual(row['snapshot_valid_until'], '2026-12-31')
            self.assertTrue(row['current_effective'])
            self.assertTrue(row['legal_source_url'].startswith('https://www.wien.gv.at/'))
        self.assertIn('separate water meter charge', rates['water']['note'])
        self.assertIn('if connected', rates['water']['note'])
        self.assertIn('if connected', rates['sewer']['note'])

    def test_waste_exact_table_amount_is_single_bin_reference_not_building_bill(self):
        row = next(row for row in self.lookup()['items'] if row['key'] == 'waste_120l_52')
        self.assertEqual(row['price'], 288.9744)
        self.assertEqual(row['price_decimal'], '288.9744')
        self.assertEqual(row['unit'], 'EUR/bin/year')
        self.assertEqual((row['bin_count'], row['bin_litres'], row['emptyings_per_year']), (1, 120, 52))
        self.assertEqual(row['price_basis'], 'published_table_reference')
        self.assertIn('reference example', row['note'])
        self.assertIn('assigned_bin_count', row['required_inputs'])

    def test_country_city_labels_and_unknown_jurisdictions_never_unlock_vienna_prices(self):
        for jurisdiction in (None, {}, {'known': False, 'country': 'AT', 'state': 'Vienna'},
                             {'known': 'true', 'country': 'AT', 'state': 'Vienna'},
                             {'known': 1, 'country': 'AT', 'state': 'Vienna'},
                             {'known': True, 'country': 'AT', 'state': 'Lower Austria'},
                             {'known': True, 'country': 'DE', 'state': 'Vienna'},
                             {'country': 'AT', 'city': 'Vienna'}):
            with self.subTest(jurisdiction=jurisdiction):
                result = self.lookup(jurisdiction, country='AT')
                self.assertEqual(result['status'], 'unsupported')
                self.assertIsNone(result['municipal_jurisdiction'])
                self.assertTrue(all(row['price'] is None for row in result['items']))
                self.assertTrue(all(row['price_decimal'] is None for row in result['items'][:3]))
                self.assertTrue(all(not row['current_effective'] for row in result['items']))

    def test_missing_or_boundary_district_match_suppresses_numeric_prices(self):
        jurisdiction = {'known': True, 'country': 'AT', 'state': 'Vienna',
                        'districts': [{'number': 1, 'boundary': True}, {'number': 3, 'boundary': True}]}
        for districts in (jurisdiction['districts'], [], [{'number': 1}],
                          [{'number': True, 'boundary': False}], [{'number': 24, 'boundary': False}],
                          [None], [{'number': 1, 'boundary': False}, {'number': 3, 'boundary': True}]):
            jurisdiction['districts'] = districts
            with self.subTest(districts=districts):
                result = self.lookup(jurisdiction)
                self.assertEqual(result['status'], 'unsupported')
                self.assertTrue(all(row['price'] is None for row in result['items']))
                self.assertTrue(result['items'][4]['source_url'].startswith('https://www.wienenergie.at/'))

    def test_expired_snapshot_retains_sources_without_current_numeric_prices(self):
        result = self.lookup(now=datetime(2027, 1, 1, tzinfo=UTC))
        self.assertEqual(result['status'], 'expired')
        for row in result['items'][:3]:
            self.assertEqual(row['status'], 'expired')
            self.assertIsNone(row['price'])
            self.assertFalse(row['current_effective'])
            self.assertIsNone(row['effective_until'])
            self.assertIn('historical source amount', row['note'])
            self.assertTrue(row['source_url'].startswith('https://www.wien.gv.at/'))
        self.assertEqual(result['items'][3]['status'], 'contract_quote_required')

    def test_review_boundary_uses_vienna_calendar_date_not_utc_or_callers_zone(self):
        before = datetime(2026, 12, 31, 22, 59, 59, tzinfo=UTC)
        after = datetime(2026, 12, 31, 23, tzinfo=UTC)
        self.assertEqual(self.lookup(now=before)['status'], 'available')
        self.assertEqual(self.lookup(now=after)['status'], 'expired')
        same_instant = before.astimezone(timezone(timedelta(hours=9)))
        self.assertEqual(same_instant.date().isoformat(), '2027-01-01')
        self.assertEqual(self.lookup(now=same_instant)['status'], 'available')

    def test_dates_before_the_published_schedule_have_no_numeric_price(self):
        result = self.lookup(now=datetime(2024, 12, 31, 22, 59, tzinfo=UTC))
        self.assertEqual(result['status'], 'not_yet_effective')
        self.assertTrue(all(row['price'] is None for row in result['items']))
        self.assertEqual(self.lookup(now=datetime(2024, 12, 31, 23, tzinfo=UTC))['status'], 'available')

    def test_gas_and_heat_never_invent_contract_or_numeric_tariff(self):
        result = self.lookup()
        for row in result['items'][3:]:
            self.assertEqual(row['status'], 'contract_quote_required')
            self.assertIsNone(row['price'])
            self.assertIsNone(row['price_decimal'])
            self.assertIsNone(row['unit'])
            self.assertIsNone(row['effective_from'])
            self.assertFalse(row['current_effective'])
            self.assertEqual(row['price_basis'], 'contract_quote_required')
            self.assertTrue(row['required_inputs'])
        self.assertTrue(result['items'][4]['source_url'].startswith('https://www.wienenergie.at/'))

    def test_austria_gas_link_survives_unavailable_vienna_planning_and_heat_is_guidance_only(self):
        result = self.lookup({'known': False}, country='AT')
        gas, heat = result['items'][3:]
        self.assertEqual(gas['status'], 'contract_quote_required')
        self.assertEqual(gas['source_name'], 'E-Control — official Austrian gas tariff comparison')
        self.assertIn('postcode', gas['required_inputs'])
        self.assertTrue(heat['source_url'].startswith('https://www.oesterreich.gv.at/'))
        self.assertIn('not a district-heating tariff comparison', heat['note'])
        self.assertNotIn('additional_sources', gas)

    def test_outside_austria_has_no_provider_connection_or_prices_assumed(self):
        result = self.lookup({'known': False}, country='DE')
        self.assertTrue(all(row['status'] == 'unsupported' for row in result['items']))
        self.assertTrue(all(row['price'] is None for row in result['items']))

    def test_return_values_are_independent_and_json_serializable(self):
        first = self.lookup(); first['items'][0]['price'] = 999
        first['items'][0]['additional_sources'].clear()
        first['items'][2]['required_inputs'].clear()
        second = self.lookup()
        self.assertEqual(second['items'][0]['price'], 2.27)
        self.assertTrue(second['items'][0]['additional_sources'])
        self.assertTrue(second['items'][2]['required_inputs'])
        json.dumps(second, allow_nan=False)

    def test_every_required_input_has_a_readable_ordered_label_with_units(self):
        for result in (self.lookup(), self.lookup({}, country='AT'),
                       self.lookup(now=datetime(2027, 1, 1, tzinfo=UTC))):
            labels = {}
            for item in result['items']:
                self.assertEqual(len(item['required_inputs']), len(item['required_input_labels']))
                for key, label in zip(item['required_inputs'], item['required_input_labels']):
                    self.assertIsInstance(label, str)
                    self.assertTrue(label.strip())
                    self.assertNotIn('_', label)
                    labels[key] = label
            self.assertEqual(labels['metered_consumption_m3'], 'Metered consumption (m³)')
            self.assertEqual(labels['annual_consumption_kwh'], 'Annual consumption (kWh)')
            self.assertEqual(labels['assigned_emptyings_per_year'], 'Collections per year')
            self.assertEqual(labels['chargeable_discharge_m3'], 'Chargeable discharge (m³)')

    def test_naive_now_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'timezone-aware'):
            self.lookup(now=NOW.replace(tzinfo=None))


if __name__ == '__main__':
    unittest.main()
