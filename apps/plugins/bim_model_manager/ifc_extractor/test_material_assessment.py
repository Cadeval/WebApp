import json
import tempfile
import unittest
from pathlib import Path

from .material_assessment import AssessmentOptions, MaterialAssessment, load_reference, replacement_count


def reference():
    return {
        'Beton bewehrt': {'Dichte':2400, 'GWP':0.224,'AP':0.000613,'PENRT':2.4,'Nutzungsdauer':100,
            'Verwertungspotential':2,'Abfallreduktion':50,'Recycling':50,'Kombination':'Bitum. Abdichtung; Verputz, Gips',
            'NEU Verwertungspotential':3,'NEU Abfallreduktion':75,'NEU Recycling':25,
            'Brutto':229,'Brutto (Wien)':274.571,'Netto (Wien)':231.407,'Einheit':'m³'},
        'Bitum. Abdichtung': {'Dichte':1050, 'GWP':0.43,'AP':0.00537,'PENRT':48.9,'Nutzungsdauer':35,
            'Verwertungspotential':1,'Abfallreduktion':25,'Recycling':75,'Kombination':'Beton',
            'NEU Verwertungspotential':2,'NEU Abfallreduktion':50,'NEU Recycling':50,
            'Brutto':8.5,'Brutto (Wien)':10.1915,'Netto (Wien)':8.6328,'Einheit':'m'},
    }


class MaterialPassportTests(unittest.TestCase):
    def test_element_combinations_and_replacements_reconcile_at_building_level(self):
        a=MaterialAssessment(reference(),AssessmentOptions(grade_weighting='mass'))
        a.add_element('wall','IfcWall',{'Beton bewehrt':3,'Bitum. Abdichtung':1},area=20,length=5)
        r=a.report()
        self.assertTrue(r['complete'])
        self.assertEqual(r['building']['mass'],8250)
        self.assertEqual(r['building']['mass_observation'],9300)
        self.assertAlmostEqual(r['building']['gwp_a1_a3'],2064.3)
        self.assertAlmostEqual(r['building']['gwp_a1_a3_b4'],2515.8)
        self.assertAlmostEqual(r['building']['ap_a1_a3_b4'],15.6906)
        self.assertAlmostEqual(r['building']['waste_mass'],5925)
        self.assertAlmostEqual(r['building']['recyclable_mass'],2325)
        self.assertAlmostEqual(r['building']['recycling_grade'],23700/8250)
        self.assertAlmostEqual(r['materials']['Beton bewehrt']['global_brutto_price'],687)
        self.assertAlmostEqual(r['materials']['Bitum. Abdichtung']['global_brutto_price'],42.5)
        self.assertTrue(all(row['combination_adjusted'] for row in r['rows']))

    def test_adjustments_apply_only_to_affected_element(self):
        a=MaterialAssessment(reference(),AssessmentOptions(grade_weighting='equal'))
        a.add_element('wall1','IfcWall',{'Beton bewehrt':3,'Bitum. Abdichtung':1},area=20,length=5)
        a.add_element('wall2','IfcWall',{'Beton bewehrt':1},area=10,length=2)
        r=a.report()
        self.assertEqual([row['combination_adjusted'] for row in r['rows']],[True,True,False])
        self.assertEqual(r['building']['mass'],10650)
        self.assertAlmostEqual(r['materials']['Beton bewehrt']['recycling_grade'],2.75)
        self.assertAlmostEqual(r['building']['recycling_grade'],2.375)

    def test_missing_reference_remains_visible_and_cannot_lower_total(self):
        a=MaterialAssessment(reference())
        a.add_element('wall','IfcWall',{'Beton bewehrt':3,'Unknown':1},area=20,length=5)
        r=a.report()
        self.assertFalse(r['complete'])
        self.assertIsNone(r['building']['mass'])
        self.assertEqual(r['building']['mass_known_subtotal'],7200)
        self.assertEqual(r['rows'][1]['issues'],['Unmatched material reference'])
        self.assertIsNone(r['building']['recycling_grade'])

    def test_blank_density_does_not_become_zero(self):
        c=reference();c['Beton bewehrt']['Dichte']=''
        a=MaterialAssessment(c);a.add_element('wall','IfcWall',{'Beton bewehrt':3},area=20,length=5)
        self.assertIsNone(a.report()['materials']['Beton bewehrt']['mass'])

    def test_negative_gwp_and_mass_price_basis(self):
        c=reference();v=c['Beton bewehrt'];v['GWP']=-1.19;v['Einheit']='kg';v['Brutto']=2
        a=MaterialAssessment(c);a.add_element('wall','IfcWall',{'Beton bewehrt':1},area=20,length=5)
        r=a.report()['building'];self.assertEqual(r['gwp_a1_a3'],-2856);self.assertEqual(r['global_brutto_price'],4800)

    def test_observation_boundary_is_configurable(self):
        self.assertEqual(replacement_count(50,AssessmentOptions()),0)
        self.assertEqual(replacement_count(50,AssessmentOptions(include_endpoint=True)),1)
        self.assertEqual(replacement_count(25,AssessmentOptions()),1)
        self.assertEqual(replacement_count(25,AssessmentOptions(include_endpoint=True)),2)
        self.assertEqual(replacement_count(100,AssessmentOptions()),0)
        self.assertEqual(replacement_count(10,AssessmentOptions()),4)

    def test_area_price_basis_and_missing_area(self):
        c=reference();c['Beton bewehrt']['Einheit']='m²'
        a=MaterialAssessment(c);a.add_element('wall','IfcWall',{'Beton bewehrt':1},area=20,length=5)
        self.assertEqual(a.report()['building']['global_brutto_price'],4580)
        a=MaterialAssessment(c);a.add_element('wall','IfcWall',{'Beton bewehrt':1},length=5)
        self.assertIsNone(a.report()['building']['global_brutto_price'])

    def test_alias_columns_cannot_silently_overwrite_coefficients(self):
        c=reference();c['Beton bewehrt']['Dichte kg/m³']=1
        with self.assertRaisesRegex(ValueError,'Duplicate configuration column'):
            MaterialAssessment(c)

    def test_windows_and_doors_excluded_without_missing_reference_errors(self):
        a=MaterialAssessment({});a.add_element('window','IfcWindow',{'Glass':1})
        r=a.report();self.assertEqual(len(r['excluded']),1);self.assertEqual(r['rows'],[])
        self.assertIsNone(r['building']['mass']);self.assertFalse(r['complete'])

    def test_duplicate_material_keys_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'r.csv';path.write_text('Material;Dichte\nA;2\nA;3\n')
            with self.assertRaisesRegex(ValueError,'duplicate'):load_reference(path)

    def test_combination_semicolons_preserve_names_with_commas(self):
        c=reference();c['Verputz, Gips']=dict(c['Bitum. Abdichtung'])
        a=MaterialAssessment(c);a.add_element('wall','IfcWall',{'Beton bewehrt':3,'Verputz, Gips':1},area=20,length=5)
        self.assertTrue(a.report()['rows'][0]['combination_adjusted'])

    def test_invalid_percentages_unavailable_and_explicit_zero_valid(self):
        c=reference();c['Beton bewehrt']['Recycling']=0;c['Beton bewehrt']['Abfallreduktion']=100
        a=MaterialAssessment(c);a.add_element('wall','IfcWall',{'Beton bewehrt':1},area=20,length=5)
        self.assertEqual(a.report()['building']['recyclable_mass'],0)
        c['Beton bewehrt']['Recycling']=200
        a=MaterialAssessment(c);a.add_element('wall','IfcWall',{'Beton bewehrt':1},area=20,length=5)
        self.assertIsNone(a.report()['building']['recyclable_mass'])


class IfcPassportTests(unittest.TestCase):
    def model(self, millimetres=False, ifc_class='IfcWall'):
        import ifcopenshell.api.project
        import ifcopenshell.api.root
        import ifcopenshell.api.context
        import ifcopenshell.api.unit
        import ifcopenshell.api.geometry
        import ifcopenshell.api.material
        import ifcopenshell.api.pset
        f=ifcopenshell.api.project.create_file(version='IFC4')
        ifcopenshell.api.root.create_entity(f,ifc_class='IfcProject',name='Passport test')
        unit=ifcopenshell.api.unit.add_si_unit(f,unit_type='LENGTHUNIT',prefix='MILLI' if millimetres else None)
        ifcopenshell.api.unit.assign_unit(f,units=[unit])
        context=ifcopenshell.api.context.add_context(f,context_type='Model')
        body=ifcopenshell.api.context.add_context(f,context_type='Model',context_identifier='Body',target_view='MODEL_VIEW',parent=context)
        wall=ifcopenshell.api.root.create_entity(f,ifc_class=ifc_class,name='Test wall')
        representation=ifcopenshell.api.geometry.add_wall_representation(f,context=body,length=5,height=4,thickness=.2)
        ifcopenshell.api.geometry.assign_representation(f,product=wall,representation=representation)
        ifcopenshell.api.geometry.edit_object_placement(f,product=wall)
        materials=ifcopenshell.api.material.add_material_set(f,set_type='IfcMaterialLayerSet')
        for name,thickness in [('Beton bewehrt',.15),('Bitum. Abdichtung',.05)]:
            m=ifcopenshell.api.material.add_material(f,name=name)
            layer=ifcopenshell.api.material.add_layer(f,layer_set=materials,material=m)
            layer.LayerThickness=thickness*(1000 if millimetres else 1)
        ifcopenshell.api.material.assign_material(f,products=[wall],type='IfcMaterialLayerSet',material=materials)
        qto=ifcopenshell.api.pset.add_qto(f,product=wall,name='Qto_WallBaseQuantities')
        ifcopenshell.api.pset.edit_qto(f,qto=qto,properties={'GrossVolume':4*(1e9 if millimetres else 1),'GrossSideArea':20*(1e6 if millimetres else 1),'Length':5*(1000 if millimetres else 1)})
        return f

    def test_actual_ifc_geometry_quantities_and_units(self):
        from .ifc_assessment import assess_ifc
        for millimetres in (False,True):
            with self.subTest(millimetres=millimetres),tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'model.ifc';self.model(millimetres).write(str(path))
                r=assess_ifc(path,reference(),AssessmentOptions(grade_weighting='mass'))
                self.assertTrue(r['complete'])
                self.assertAlmostEqual(r['building']['mass'],8250)
                self.assertAlmostEqual(r['building']['gwp_a1_a3_b4'],2515.8)
                self.assertAlmostEqual(r['building']['global_brutto_price'],729.5)
                json.dumps(r,allow_nan=False)


if __name__=='__main__':unittest.main()
