import copy
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.plugin_manager.models import PluginRecord, UserPluginSelection
from apps.plugins.bim_model_manager import PLUGIN_ID
from apps.plugins.bim_model_manager.ifc_extractor.material_assessment import AssessmentOptions, MaterialAssessment, file_hash
from apps.plugins.bim_model_manager.ifc_extractor.test_material_assessment import IfcPassportTests, reference
from apps.plugins.bim_model_manager.django.models import FileUpload, CadevilDocument, BuildingMetrics
from apps.plugins.bim_model_manager.viewer_materials import source_materials, extract_source_materials, attach_assessment
from tests.bolt_browser import BoltBrowser


def saved_report(identifier, digest):
    assessment = MaterialAssessment(reference(), AssessmentOptions(grade_weighting='mass'))
    assessment.add_element(identifier, 'IfcWall', {'Beton bewehrt': 3, 'Bitum. Abdichtung': 1}, area=20, length=5)
    # Whole-building material totals must not be shown as the clicked wall's values.
    assessment.add_element('another-element', 'IfcWall', {'Beton bewehrt': 30}, area=200, length=50)
    report = assessment.report()
    report['provenance'] = {'ifc_sha256': digest, 'configuration': reference()}
    return report


class ViewerSourceMetadataTests(SimpleTestCase):
    def same_name_materials(self):
        model = IfcPassportTests().model()
        materials = model.by_type('IfcMaterial')
        for material, density, description in zip(materials, (2400.0, 1200.0), ('Dense declaration', 'Light declaration')):
            material.Name = 'Beton bewehrt'
            material.Description = description
            model.create_entity('IfcMaterialProperties', Name='Physical properties', Material=material,
                Properties=[model.create_entity('IfcPropertySingleValue', Name='MassDensity',
                    NominalValue=model.create_entity('IfcMassDensityMeasure', density))])
        return model

    def test_same_name_distinct_material_declarations_preserve_values_and_layer_contexts(self):
        model = self.same_name_materials()
        identifier = model.by_type('IfcWall')[0].GlobalId
        data = extract_source_materials(model, 'hash')
        cards = data['elements'][identifier]['materials']
        self.assertEqual(len(cards), 2)
        self.assertEqual({m['material_id'] for m in cards}, {m.id() for m in model.by_type('IfcMaterial')})
        self.assertEqual([next(p['value'] for p in m['properties'] if p['label'] == 'Mass Density') for m in cards], [2400, 1200])
        self.assertEqual([next(p['value'] for p in m['properties'] if p['label'] == 'Description') for m in cards],
                         ['Dense declaration', 'Light declaration'])
        self.assertEqual({m['member_contexts'][0]['member_id'] for m in cards},
                         {layer.id() for layer in model.by_type('IfcMaterialLayer')})
        summary = extract_source_materials(model, 'hash', summary=True)
        self.assertEqual([m['material_id'] for m in summary['elements'][identifier]['materials']],
                         [m['material_id'] for m in cards])
        self.assertTrue(all(not m['properties'] for m in summary['elements'][identifier]['materials']))
        self.assertTrue(all('member_contexts' not in m for m in summary['elements'][identifier]['materials']))

    def test_name_group_assessment_totals_are_shown_once_without_claiming_one_declaration(self):
        model = self.same_name_materials()
        identifier = model.by_type('IfcWall')[0].GlobalId
        assessment = MaterialAssessment(reference())
        assessment.add_element(identifier, 'IfcWall', {'Beton bewehrt': 4}, area=20, length=5)
        report = assessment.report(); report['provenance'] = {'configuration': reference()}
        source = extract_source_materials(model, 'hash')
        data = attach_assessment(source, report)
        materials = data['elements'][identifier]['materials']
        source_cards = [m for m in materials if m['association'] != 'assessment']
        groups = [m for m in materials if m['association'] == 'assessment']
        self.assertEqual(len(source_cards), 2)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['source_material_ids'], [m['material_id'] for m in source_cards])
        self.assertEqual(groups[0]['assessment']['scope'], 'element material name group')
        self.assertTrue(all(m['assessment']['status'] == 'shared' and not m['assessment']['properties'] for m in source_cards))
        properties = {p['key']: p for p in groups[0]['assessment']['properties']}
        self.assertEqual(properties['mass']['value'], 9600)
        self.assertEqual(properties['global_brutto_price']['value'], 916)
        self.assertEqual(properties['global_brutto_price']['unit'], 'EUR')
        self.assertEqual(properties['mass']['scope'], 'element material name group')
        self.assertNotIn('assessment', source['elements'][identifier])

    def test_archicad_complex_component_quantities_are_flattened_with_units(self):
        model = IfcPassportTests().model()
        wall = model.by_type('IfcWall')[0]
        unit = model.create_entity('IfcSIUnit', UnitType='VOLUMEUNIT', Name='CUBIC_METRE')
        quantity = model.create_entity('IfcQuantityVolume', Name='NetVolume', VolumeValue=2.5, Unit=unit)
        component = model.create_entity('IfcPhysicalComplexQuantity', Name='Concrete component',
            HasQuantities=[quantity], Discrimination='material')
        qto = model.create_entity('IfcElementQuantity', GlobalId=__import__('ifcopenshell').guid.new(),
            Name='Component Quantities', Quantities=[component])
        model.create_entity('IfcRelDefinesByProperties', GlobalId=__import__('ifcopenshell').guid.new(),
            RelatedObjects=[wall], RelatingPropertyDefinition=qto)
        data = extract_source_materials(model, 'hash')
        prop = next(p for p in data['elements'][wall.GlobalId]['properties']
                    if p['label'] == 'Concrete component / Net Volume')
        self.assertEqual(prop['value'], 2.5)
        self.assertEqual(prop['unit'], 'm3')
        self.assertEqual(data['source']['status'], 'complete')

    def test_declared_material_properties_layers_and_units_are_retained(self):
        model = IfcPassportTests().model(millimetres=True)
        wall = model.by_type('IfcWall')[0]
        material = model.by_type('IfcMaterial')[0]
        model.create_entity('IfcMaterialProperties', Name='Physical properties', Material=material,
            Properties=[model.create_entity('IfcPropertySingleValue', Name='DeclaredThickness',
                NominalValue=model.create_entity('IfcLengthMeasure', 20.0))])
        data = extract_source_materials(model, 'test-hash')
        first = data['elements'][wall.GlobalId]['materials'][0]
        self.assertEqual(first['name'], 'Beton bewehrt')
        self.assertEqual(first['association'], 'layer')
        props = {p['label']: p for p in first['properties']}
        self.assertEqual((props['Layer thickness']['value'], props['Layer thickness']['unit']), (150, 'mm'))
        self.assertEqual((props['Declared Thickness']['value'], props['Declared Thickness']['unit']), (20, 'mm'))
        self.assertEqual(props['Declared Thickness']['source'], 'IFC')
        self.assertTrue(any(p['label'] == 'Gross Volume' for p in data['elements'][wall.GlobalId]['properties']))

    def test_cache_uses_source_hash_preserves_bytes_and_rebuilds_corrupt_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'model.ifc'
            cache = Path(directory) / 'cache'
            model = IfcPassportTests().model(); model.write(str(path))
            original = path.read_bytes()
            first = source_materials(path, cache)
            with patch('apps.plugins.bim_model_manager.viewer_materials.ifcopenshell.open', side_effect=AssertionError('cache miss')):
                self.assertEqual(source_materials(path, cache), first)
            self.assertEqual(path.read_bytes(), original)
            next(cache.glob('*.json')).write_text('{broken', encoding='utf-8')
            self.assertEqual(source_materials(path, cache), first)
            model.by_type('IfcMaterial')[0].Name = 'Changed material'; model.write(str(path))
            updated = source_materials(path, cache)
            self.assertNotEqual(first['source']['sha256'], updated['source']['sha256'])
            self.assertEqual(len(list(cache.glob('*.json'))), 2)

    def test_summary_avoids_loading_properties_and_element_detail_is_cached_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'model.ifc'
            model = IfcPassportTests().model(); model.write(str(path))
            identifier = model.by_type('IfcWall')[0].GlobalId
            with patch('apps.plugins.bim_model_manager.viewer_materials._properties', side_effect=AssertionError('summary property scan')):
                summary = source_materials(path, Path(directory) / 'cache')
            self.assertEqual(summary['mode'], 'summary')
            self.assertEqual(summary['elements'][identifier]['properties'], [])
            detail = source_materials(path, Path(directory) / 'cache', element_id=identifier)
            self.assertEqual(set(detail['elements']), {identifier})
            self.assertTrue(detail['elements'][identifier]['properties'])
            with patch('apps.plugins.bim_model_manager.viewer_materials.ifcopenshell.open', side_effect=AssertionError('detail cache miss')):
                self.assertEqual(source_materials(path, Path(directory) / 'cache', element_id=identifier), detail)
            with self.assertRaises(ValueError):
                source_materials(path, Path(directory) / 'cache', element_id='../outside')

    def test_bad_material_association_is_nonfatal_and_properties_survive(self):
        model = IfcPassportTests().model()
        with patch('apps.plugins.bim_model_manager.viewer_materials.ifcopenshell.util.element.get_material', side_effect=RuntimeError('Invalid material')):
            data = extract_source_materials(model, 'hash')
        wall = data['elements'][model.by_type('IfcWall')[0].GlobalId]
        self.assertTrue(wall['properties'])
        self.assertEqual(wall['materials'], [])
        self.assertEqual(data['source']['status'], 'partial')
        self.assertEqual(data['source']['warnings'][0]['message'], 'The IFC material association is unreadable.')

    def test_assessment_enrichment_does_not_mutate_source_cache(self):
        model = IfcPassportTests().model()
        identifier = model.by_type('IfcWall')[0].GlobalId
        source = extract_source_materials(model, 'hash')
        original = copy.deepcopy(source)
        data = attach_assessment(source, saved_report(identifier, 'hash'))
        self.assertEqual(source, original)
        wall = data['elements'][identifier]
        totals = {p['key']: p for p in wall['assessment']['properties']}
        self.assertEqual(totals['mass']['value'], 8250)
        beton = next(m for m in wall['materials'] if m['name'] == 'Beton bewehrt')
        props = {p['key']: p for p in beton['assessment']['properties']}
        self.assertEqual(props['mass']['value'], 7200)
        self.assertEqual(props['global_brutto_price']['value'], 687)
        self.assertEqual(props['global_brutto_price']['unit'], 'EUR')
        self.assertEqual(props['dichte']['value'], 2400)
        self.assertEqual(props['mass']['scope'], 'element material component')
        self.assertNotEqual(props['mass']['value'], saved_report(identifier, 'hash')['materials']['Beton bewehrt']['mass'])

    def test_excluded_part_has_explicit_unavailable_quantities_without_zero_substitution(self):
        model = IfcPassportTests().model(ifc_class='IfcWindow')
        identifier = model.by_type('IfcWindow')[0].GlobalId
        source = extract_source_materials(model, 'hash')
        report = {'rows': [], 'elements': {}, 'excluded': [{'element_id': identifier, 'ifc_class': 'IfcWindow'}]}
        data = attach_assessment(source, report)
        assessment = data['elements'][identifier]['assessment']
        self.assertEqual(assessment['status'], 'excluded')
        self.assertTrue(all(p['value'] is None for p in assessment['properties']))
        self.assertIn('excluded', assessment['issues'][0])


class ViewerMaterialEndpointTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        setting = override_settings(MEDIA_ROOT=self.directory.name)
        setting.enable(); self.addCleanup(setting.disable)
        self.user = get_user_model().objects.create_user(username='viewer-materials')
        self.other = get_user_model().objects.create_user(username='other-viewer-materials')
        self.plugin, _ = PluginRecord.objects.update_or_create(plugin_id=PLUGIN_ID, defaults={'enabled': True})
        for user in (self.user, self.other):
            UserPluginSelection.objects.create(user=user, plugin=self.plugin)
        source = Path(self.directory.name) / 'original.ifc'
        model = IfcPassportTests().model(); model.write(str(source))
        self.identifier = model.by_type('IfcWall')[0].GlobalId
        self.upload = FileUpload.objects.create(user=self.user,
            document=SimpleUploadedFile('material-model.ifc', source.read_bytes()))
        self.digest = file_hash(Path(self.upload.document.path))
        self.path = reverse('bim:viewer_materials', args=[self.upload.pk])
        self.client = BoltBrowser(); self.addCleanup(self.client.close); self.client.force_login(self.user)

    def assessment(self, *, user=None, upload=None, report=None):
        user = user or self.user
        document = CadevilDocument.objects.create(user=user, group=user.groups.first(),
            upload=upload or self.upload, description='Controlled assessment')
        BuildingMetrics.objects.create(project=document, assessment_report=report or saved_report(self.identifier, self.digest))
        return document

    def test_owned_metadata_without_assessment_and_invalid_source_response(self):
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['assessment']['status'], 'none')
        self.assertEqual(data['elements'][self.identifier]['materials'][0]['name'], 'Beton bewehrt')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertFalse(CadevilDocument.objects.exists())
        broken = FileUpload.objects.create(user=self.user, document=SimpleUploadedFile('bad.ifc', b'not IFC'))
        self.assertEqual(self.client.get(reverse('bim:viewer_materials', args=[broken.pk])).status_code, 422)

    def test_one_report_is_unambiguous_and_enrichment_is_not_cached(self):
        document = self.assessment()
        summary = self.client.get(self.path).json()
        self.assertNotIn('assessment', summary['elements'][self.identifier])
        response = self.client.get(self.path + '?element=' + self.identifier)
        data = response.json()
        self.assertEqual(data['assessment']['id'], str(document.pk))
        wall = data['elements'][self.identifier]
        self.assertEqual(next(p['value'] for p in wall['assessment']['properties'] if p['key'] == 'mass'), 8250)
        cache = next((Path(self.directory.name) / 'bim-viewer-cache').glob('materials-*.json'))
        self.assertNotIn('assessment', json.loads(cache.read_text()))
        self.assertNotIn('assessment', json.loads(cache.read_text())['elements'][self.identifier])
        page = self.client.get(reverse('bim:viewer', args=[self.upload.pk]))
        self.assertEqual(page.status_code, 200)

    def test_multiple_reports_require_explicit_selection_and_match_requested_report(self):
        first = self.assessment()
        changed = saved_report(self.identifier, self.digest)
        changed['rows'][0]['global_brutto_price'] = 123
        second = self.assessment(report=changed)
        data = self.client.get(self.path).json()
        self.assertEqual(data['assessment']['status'], 'choose')
        self.assertEqual({o['id'] for o in data['assessment_options']}, {str(first.pk), str(second.pk)})
        self.assertNotIn('assessment', data['elements'][self.identifier])
        chosen = self.client.get(self.path + '?element=' + self.identifier + '&assessment=' + str(second.pk)).json()
        beton = chosen['elements'][self.identifier]['materials'][0]
        self.assertEqual(next(p['value'] for p in beton['assessment']['properties'] if p['key'] == 'global_brutto_price'), 123)

    def test_cross_owner_cross_upload_and_runtime_plugin_gate(self):
        foreign = self.assessment(user=self.other)
        own_other_upload = FileUpload.objects.create(user=self.user,
            document=SimpleUploadedFile('other.ifc', Path(self.upload.document.path).read_bytes()))
        different = self.assessment(upload=own_other_upload)
        for value in (foreign.pk, different.pk, 'not-a-uuid'):
            self.assertEqual(self.client.get(self.path + '?assessment=' + str(value)).status_code, 404)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.path).status_code, 404)
        self.client.force_login(self.user)
        UserPluginSelection.objects.filter(user=self.user, plugin=self.plugin).delete()
        self.assertEqual(self.client.get(self.path).status_code, 404)
        UserPluginSelection.objects.create(user=self.user, plugin=self.plugin)
        PluginRecord.objects.filter(pk=self.plugin.pk).update(enabled=False)
        self.assertEqual(self.client.get(self.path).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self.path).status_code, 302)

    def test_mismatching_or_missing_source_hash_does_not_attach_assessment_values(self):
        report = saved_report(self.identifier, 'different-source')
        document = self.assessment(report=report)
        data = self.client.get(self.path).json()
        self.assertEqual(data['assessment']['status'], 'source_mismatch')
        self.assertNotIn('assessment', data['elements'][self.identifier])
        metric = BuildingMetrics.objects.get(project=document)
        report['provenance'].pop('ifc_sha256'); metric.assessment_report = report; metric.save()
        data = self.client.get(self.path).json()
        self.assertEqual(data['assessment']['status'], 'unverified')
        self.assertNotIn('assessment', data['elements'][self.identifier])
