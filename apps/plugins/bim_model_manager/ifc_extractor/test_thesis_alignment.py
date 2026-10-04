import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from .material_assessment import MaterialAssessment, AssessmentOptions
from .test_material_assessment import reference, IfcPassportTests
from apps.plugins.bim_model_manager.assessment_presentation import charts, comparison


class ThesisAlignmentTests(unittest.TestCase):
    def report(self):
        assessment=MaterialAssessment(reference(),AssessmentOptions(grade_weighting='mass'))
        assessment.add_element('wall','IfcWall',{'Beton bewehrt':3,'Bitum. Abdichtung':1},area=20,length=5)
        return assessment

    def test_element_totals_reconcile_initial_and_replacement_impacts(self):
        report=self.report().report()
        element=report['elements']['wall']
        self.assertEqual(element['mass'],8250)
        self.assertEqual(element['mass_observation'],9300)
        self.assertEqual(element['waste_mass_observation'],6450)
        self.assertAlmostEqual(element['gwp_a1_a3_b4'],2515.8)
        self.assertAlmostEqual(element['ap_a1_a3_b4'],15.6906)
        self.assertAlmostEqual(element['penrt_a1_a3_b4'],119970)

    def test_lca_averaging_is_explicit_and_denominators_remain_traceable(self):
        for policy,denominator in [('installed_mass',8250),('material_mean',2)]:
            assessment=MaterialAssessment(reference(),AssessmentOptions(lca_averaging=policy))
            assessment.add_element('wall','IfcWall',{'Beton bewehrt':3,'Bitum. Abdichtung':1},area=20,length=5)
            averages=assessment.report()['lca_averages']
            self.assertEqual(averages['denominator'],denominator)
            self.assertAlmostEqual(averages['values']['gwp_a1_a3'],2064.3/denominator)
            self.assertAlmostEqual(averages['values']['gwp_a1_a3_b4'],2515.8/denominator)
            assessment.add_element('unknown','IfcColumn',{})
            self.assertIsNone(assessment.report()['lca_averages']['values']['gwp_a1_a3'])
        self.assertIsNone(self.report().report()['lca_averages']['values']['gwp_a1_a3'])

    def test_omitted_element_cannot_produce_apparently_full_building_totals(self):
        assessment=self.report()
        assessment.add_element('missing','IfcColumn',{})
        report=assessment.report()
        self.assertFalse(report['complete'])
        self.assertIsNone(report['building']['mass'])
        self.assertIsNone(report['building']['local_brutto_price'])
        self.assertEqual(report['building']['mass_known_subtotal'],8250)

    def test_signed_chart_distinguishes_negative_zero_and_unavailable(self):
        chart=next(c for c in charts([('wood',{'gwp_a1_a3':-20}),('zero',{'gwp_a1_a3':0}),('missing',{})]) if c['key']=='gwp_a1_a3')
        negative,zero,missing=chart['bars']
        self.assertTrue(negative['available']);self.assertGreater(negative['width'],0)
        self.assertLess(negative['x'],chart['baseline'])
        self.assertTrue(zero['available']);self.assertEqual(zero['width'],0)
        self.assertFalse(missing['available']);self.assertIsNone(missing['value'])

    def test_comparison_requires_matching_reference_and_boundaries(self):
        a=self.report().report();a['provenance']={'configuration_sha256':'same'}
        doc=SimpleNamespace(description='A',pk='A')
        other=SimpleNamespace(description='B',pk='B')
        b=copy.deepcopy(a)
        self.assertTrue(comparison([(doc,a),(other,b)])['comparable'])
        b['options']['include_endpoint']=True
        self.assertFalse(comparison([(doc,a),(other,b)])['comparable'])
        b=copy.deepcopy(a);b['provenance']['configuration_sha256']='different'
        self.assertFalse(comparison([(doc,a),(other,b)])['comparable'])
        b=copy.deepcopy(a);b['complete']=False
        self.assertFalse(comparison([(doc,a),(other,b)])['comparable'])

    def test_window_and_door_subclasses_retain_the_thesis_exclusion(self):
        assessment=MaterialAssessment({})
        for name in ('IfcWindowStandardCase','IfcDoorStandardCase'):
            assessment.add_element(name,name,{'Unknown material':1})
        report=assessment.report()
        self.assertEqual(len(report['excluded']),2)
        self.assertEqual(report['rows'],[])
        self.assertEqual(report['issues'],[])

    def test_structural_proxy_with_materials_is_not_silently_excluded(self):
        from .ifc_assessment import assess_ifc
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'proxy.ifc'
            IfcPassportTests().model(ifc_class='IfcBuildingElementProxy').write(str(path))
            report=assess_ifc(path,reference(),AssessmentOptions(grade_weighting='mass'))
            self.assertTrue(report['complete'])
            self.assertEqual(report['building']['mass'],8250)
            self.assertEqual(report['excluded'],[])

    def test_geometry_fallback_quantities_are_readable_without_qto(self):
        import ifcopenshell.api.pset
        from .ifc_assessment import assess_ifc
        model=IfcPassportTests().model()
        wall=model.by_type('IfcWall')[0]
        qto=model.by_type('IfcElementQuantity')[0]
        ifcopenshell.api.pset.remove_pset(model,product=wall,pset=qto)
        import numpy as np
        import ifcopenshell.api.geometry
        rotation=np.array([[0,-1,0,0],[1,0,0,0],[0,0,1,0],[0,0,0,1]],dtype=float)
        ifcopenshell.api.geometry.edit_object_placement(model,product=wall,matrix=rotation)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'geometry.ifc';model.write(str(path))
            report=assess_ifc(path,reference(),AssessmentOptions(grade_weighting='mass'))
            self.assertTrue(report['complete'])
            self.assertAlmostEqual(report['building']['mass'],8250)
            self.assertAlmostEqual(report['inventory'][0]['volume_m3'],4)
            self.assertAlmostEqual(report['inventory'][0]['area_m2'],20)
            self.assertAlmostEqual(report['inventory'][0]['length_m'],5)

    def test_inventory_records_storey_and_world_overlap_without_editing_ifc(self):
        import numpy as np
        import ifcopenshell.api.root
        import ifcopenshell.api.aggregate
        import ifcopenshell.api.spatial
        import ifcopenshell.api.geometry
        from .ifc_assessment import assess_ifc
        model=IfcPassportTests().model()
        building=ifcopenshell.api.root.create_entity(model,ifc_class='IfcBuilding',name='Building')
        storey=ifcopenshell.api.root.create_entity(model,ifc_class='IfcBuildingStorey',name='Ground')
        storey.Elevation=0
        ifcopenshell.api.aggregate.assign_object(model,products=[building],relating_object=model.by_type('IfcProject')[0])
        ifcopenshell.api.aggregate.assign_object(model,products=[storey],relating_object=building)
        wall=model.by_type('IfcWall')[0]
        other=ifcopenshell.api.root.create_entity(model,ifc_class='IfcWall',name='Overlap candidate')
        representation=ifcopenshell.api.geometry.add_wall_representation(model,context=wall.Representation.Representations[0].ContextOfItems,length=5,height=4,thickness=.2)
        ifcopenshell.api.geometry.assign_representation(model,product=other,representation=representation)
        matrix=np.eye(4);matrix[0,3]=1
        ifcopenshell.api.geometry.edit_object_placement(model,product=other,matrix=matrix)
        ifcopenshell.api.spatial.assign_container(model,products=[wall,other],relating_structure=storey)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'model.ifc';model.write(str(path));original=path.read_bytes()
            report=assess_ifc(path,reference(),AssessmentOptions(grade_weighting='mass'))
            self.assertEqual(path.read_bytes(),original)
        self.assertEqual(len(report['inventory']),2)
        self.assertEqual(report['inventory'][0]['storey_name'],'Ground')
        self.assertEqual(report['inventory'][0]['storey_elevation_m'],0)
        self.assertEqual(len(report['model_diagnostics']),1)
        self.assertEqual(set(report['model_diagnostics'][0]['element_ids']),{wall.GlobalId,other.GlobalId})
        self.assertFalse(report['overlap_check']['truncated'])
        self.assertIn('ifc_assessment.py',report['provenance']['code_files_sha256'])

    def test_unreadable_geometry_and_qto_without_volume_cannot_claim_complete_zero_impact(self):
        from .ifc_assessment import assess_ifc
        model = IfcPassportTests().model()
        model.remove(model.by_type('IfcQuantityVolume')[0])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing-volume.ifc'
            model.write(str(path))
            from unittest.mock import patch
            with patch('apps.plugins.bim_model_manager.ifc_extractor.ifc_assessment.geometry_measurements', return_value=({}, {'threads': 4, 'converted': 0, 'serial_fallbacks': 1})):
                report = assess_ifc(path, reference(), AssessmentOptions(grade_weighting='mass'))
        self.assertFalse(report['complete'])
        self.assertIsNone(report['building']['mass'])
        self.assertIsNone(report['building']['gwp_a1_a3'])
        self.assertTrue(any('No positive material volume' in item['message'] for item in report['issues']))


if __name__=='__main__':unittest.main()
