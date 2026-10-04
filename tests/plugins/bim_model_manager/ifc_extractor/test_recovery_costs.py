import unittest
from plugins.bim_model_manager.ifc_extractor.recovery_costs import recovery_cost_analysis

class RecoveryCostTests(unittest.TestCase):
    def test_reconciles_costs_without_using_average_grade_as_price_multiplier(self):
        result=recovery_cost_analysis([
            {'recycling_grade':1,'global_brutto_price':100},
            {'recycling_grade':5,'global_brutto_price':900},
            {'recycling_grade':None,'global_brutto_price':50},
        ])
        self.assertEqual(result['known_cost_eur'],1050)
        self.assertEqual(result['groups'][0]['known_cost_eur'],100)
        self.assertEqual(result['groups'][4]['known_cost_eur'],900)
        self.assertEqual(result['groups'][-1]['known_cost_eur'],50)
        self.assertAlmostEqual(sum(g['share_of_known_cost_percent'] for g in result['groups']),100)

    def test_missing_prices_stay_missing_but_zero_is_known(self):
        result=recovery_cost_analysis([
            {'recycling_grade':2,'global_brutto_price':None},
            {'recycling_grade':2,'global_brutto_price':0},
        ])
        grade=result['groups'][1]
        self.assertIsNone(grade['cost_eur'])
        self.assertEqual(grade['known_cost_eur'],0)
        self.assertEqual(grade['priced_rows'],1)
        self.assertEqual(grade['missing_price_rows'],1)
        self.assertIsNone(grade['share_of_known_cost_percent'])

    def test_invalid_or_average_grades_are_ungraded(self):
        result=recovery_cost_analysis([{'recycling_grade':grade,'global_brutto_price':10} for grade in [0,6,2.5,float('nan')]])
        self.assertEqual(result['groups'][-1]['known_cost_eur'],40)

if __name__=='__main__': unittest.main()
