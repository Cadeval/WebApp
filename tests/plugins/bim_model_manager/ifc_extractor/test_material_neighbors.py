import unittest
from plugins.bim_model_manager.ifc_extractor.material_neighbors import material_classification
from tests.plugins.bim_model_manager.ifc_extractor.test_material_assessment import reference
from plugins.bim_model_manager.ifc_extractor.material_assessment import MaterialAssessment

class MaterialNeighbourTests(unittest.TestCase):
    def test_typo_suggests_reference_but_does_not_change_unknown_results(self):
        assessment=MaterialAssessment(reference())
        assessment.add_element('wall','IfcWall',{'Beton bewehr':4},area=20,length=5)
        report=assessment.report()
        material=report['material_classification']['materials'][0]
        self.assertEqual(material['reference'],None)
        self.assertEqual(material['candidates'][0]['reference'],'Beton bewehrt')
        self.assertGreater(material['candidates'][0]['similarity'],.5)
        self.assertIsNone(report['building']['mass'])
        self.assertIn('Unmatched material reference',report['rows'][0]['issues'])

    def test_exact_match_keeps_missing_properties_visible(self):
        config=reference();config['Beton bewehrt']['Dichte']=None
        result=material_classification([{'material':'Beton bewehrt','element_id':'wall'}],config)
        entry=result['materials'][0]
        self.assertEqual(entry['status'],'Exact reference match')
        self.assertIn('Dichte',entry['coverage']['missing_fields'])
        self.assertEqual(entry['candidates'],[])

    def test_case_and_accents_are_similarity_features_not_automatic_confirmation(self):
        result=material_classification([{'material':'BETON BEWEHRT','element_id':'a'},{'material':'BETON BEWEHRT','element_id':'b'}],reference())
        entry=result['materials'][0]
        self.assertEqual(entry['elements'],['a','b'])
        self.assertEqual(entry['reference'],None)
        self.assertEqual(entry['candidates'][0]['similarity'],1)

    def test_blank_name_and_empty_reference_have_no_candidates(self):
        result=material_classification([{'material':'','element_id':'a'}],{})
        self.assertEqual(result['materials'][0]['candidates'],[])
