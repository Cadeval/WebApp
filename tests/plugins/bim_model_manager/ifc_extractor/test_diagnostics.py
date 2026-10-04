import unittest
from plugins.bim_model_manager.ifc_extractor.diagnostics import schema_diagnostics, grouped_diagnostics
from tests.plugins.bim_model_manager.ifc_extractor.test_material_assessment import IfcPassportTests

class DiagnosticTests(unittest.TestCase):
    def test_repeated_rules_merge_and_retain_every_offending_product(self):
        model=IfcPassportTests().model()
        wall=model.by_type('IfcWall')[0]
        other=model.create_entity('IfcWall',GlobalId='second-wall',Name='Second wall')
        statements=[{'instance':product,'attribute':'IfcProduct.PlacementForShapeRepresentation','message':f'Rule expression\n\nViolated by:\n#{product.id()}\nOn instance:\n{product}'} for product in [wall,other,wall]]
        diagnostics=schema_diagnostics(model,statements)
        groups=grouped_diagnostics({'schema_validation':{'diagnostics':diagnostics}})
        self.assertEqual(len(groups),1)
        self.assertEqual(groups[0]['count'],3)
        self.assertEqual(len(groups[0]['elements']),2)
        self.assertEqual(groups[0]['summary'],'Geometry has a representation but no required placement')

    def test_non_product_geometry_warning_links_to_owning_wall(self):
        model=IfcPassportTests().model();wall=model.by_type('IfcWall')[0]
        diagnostics=schema_diagnostics(model,[{'instance':wall.Representation.Representations[0],'message':'Invalid representation'}])
        self.assertIn(wall.GlobalId,[e['element_id'] for e in diagnostics[0]['elements']])

    def test_material_errors_and_model_only_errors_keep_counts_and_identity(self):
        groups=grouped_diagnostics({'rows':[{'element_id':'wall-a','material':'Concrete','issues':['Missing density','Missing density']}], 'issues':[{'message':'Model metadata missing'}]})
        self.assertEqual(groups[0]['elements'],[])
        self.assertEqual(groups[1]['count'],2)
        self.assertEqual(groups[1]['elements'][0]['element_id'],'wall-a')
