import unittest
from apps.shared.ifc_extractor.element_identity import element_reference,label_references,enrich_report
from apps.shared.ifc_extractor.diagnostics import grouped_diagnostics
from apps.shared.ifc_extractor.test_material_assessment import IfcPassportTests
class ElementIdentityTests(unittest.TestCase):
 def test_name_whitespace_missing_and_duplicates(self):
  model=IfcPassportTests().model();wall=model.by_type('IfcWall')[0]
  wall.Name='  North   wall ';wall.Tag='W-1'
  self.assertEqual(element_reference(wall)['display_name'],'North wall')
  wall.Name=None
  self.assertEqual(element_reference(wall)['display_name'],'IfcWall W-1')
  wall.Tag=None
  self.assertEqual(element_reference(wall)['display_name'],'IfcWall (unnamed)')
  refs=label_references([{'element_id':'a','step_id':1,'name':'Wall','ifc_class':'IfcWall'},{'element_id':'b','step_id':2,'name':'Wall','ifc_class':'IfcWall'}])
  self.assertNotEqual(refs[0]['display_name'],refs[1]['display_name'])
 def test_reports_keep_identity_resolve_all_surfaces_without_mutation(self):
  data={'inventory':[{'element_id':'wall-a','step_id':42,'name':'West wall','ifc_class':'IfcWall'}], 'rows':[{'element_id':'wall-a','material':'Concrete','issues':['Missing density']}], 'elements':{'wall-a':{'mass':None}},'issues':[{'element_id':'wall-a','message':'Missing volume'}], 'model_diagnostics':[{'element_ids':['wall-a']}], 'material_classification':{'materials':[{'elements':['wall-a']}]}}
  report=enrich_report(data)
  self.assertNotIn('display_name',data['inventory'][0])
  for entry in [report['rows'][0],report['elements']['wall-a'],report['model_diagnostics'][0]['elements'][0],report['material_classification']['materials'][0]['element_references'][0]]:
   self.assertEqual(entry['display_name'],'West wall');self.assertEqual(entry['element_id'],'wall-a')
  for group in grouped_diagnostics(report):self.assertEqual(group['elements'][0]['display_name'],'West wall')

 def test_historical_row_names_survive_missing_inventory(self):
  data={'rows':[{'element_id':'old-guid','name':'  Old   named wall  ','ifc_class':'IfcWall','issues':['Missing density']}], 'issues':[]}
  report=enrich_report(data)
  self.assertEqual(report['rows'][0]['display_name'],'Old named wall')
  self.assertEqual(grouped_diagnostics(report)[0]['elements'][0]['display_name'],'Old named wall')
  self.assertEqual(data['rows'][0]['name'],'  Old   named wall  ')
 def test_long_name_and_same_storey_duplicates(self):
  model=IfcPassportTests().model();space=model.create_entity('IfcSpace',GlobalId='space',Name='  ',LongName='Conference room')
  self.assertEqual(element_reference(space)['display_name'],'Conference room')
  entries=[{'element_id':'a','step_id':1,'name':'Wall','storey_name':'Ground'},{'element_id':'b','step_id':2,'name':'Wall','storey_name':'Ground'}]
  refs=label_references(entries);self.assertNotEqual(refs[0]['display_name'],refs[1]['display_name'])
  refs=label_references([{'element_id':'raw-guid-a','ifc_class':'IfcWall'},{'element_id':'raw-guid-b','ifc_class':'IfcWall'}])
  self.assertTrue(all('raw-guid' not in e['display_name'] for e in refs))

 def test_archicad_uuid_tags_remain_technical(self):
  tag='0c7a9d52-6bf8-46d2-8aac-3812bb5b9c45'
  refs=label_references([{'element_id':'a','step_id':1,'name':'Decke-002','tag':tag,'storey_name':'Ground'},{'element_id':'b','step_id':2,'name':'Decke-002','tag':tag,'storey_name':'Ground'}])
  self.assertTrue(all(tag not in e['display_name'] for e in refs))
  self.assertEqual(refs[0]['tag'],tag)
  model=IfcPassportTests().model();wall=model.by_type('IfcWall')[0];wall.Name=None;wall.Tag=tag
  self.assertEqual(element_reference(wall)['display_name'],'IfcWall (unnamed)')


from django.test import SimpleTestCase
from django.template.loader import render_to_string
from types import SimpleNamespace
class IdentityTemplateTests(SimpleTestCase):
 def test_ifc_name_is_escaped_and_selection_id_is_retained(self):
  name='<script>alert(1)</script>'
  data=enrich_report({'complete':False,'options':{},'inventory':[{'element_id':'stable-id','name':name,'ifc_class':'IfcWall','step_id':7}], 'rows':[{'element_id':'stable-id','material':'Concrete','issues':[]}], 'elements':{'stable-id':{}},'model_diagnostics':[]})
  data['diagnostic_groups']=grouped_diagnostics({**data,'issues':[{'element_id':'stable-id','message':'Missing volume'}]})
  html=render_to_string('shared/material_passport_report.html',{'document':SimpleNamespace(description='Test',upload_id=123),'report':data,'elements':list(data['elements'].items()),'rows':data['rows'],'materials':[],'charts':[]})
  self.assertNotIn(name,html);self.assertIn('&lt;script&gt;',html);self.assertIn('?element=stable-id',html)
