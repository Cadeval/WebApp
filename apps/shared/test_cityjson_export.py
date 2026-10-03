import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ifcopenshell
import ifcopenshell.api.aggregate
import ifcopenshell.api.geometry
import ifcopenshell.api.spatial
import numpy as np

from .cityjson_export import CityJSONExportError, _coordinate_transform, _schema_validators, export_components, model_cityjson, validate_export
from .ifc_extractor.material_assessment import file_hash
from .ifc_extractor.test_material_assessment import IfcPassportTests


def with_building(millimetres=False):
    model = IfcPassportTests().model(millimetres=millimetres)
    project, wall = model.by_type('IfcProject')[0], model.by_type('IfcWall')[0]
    building = model.create_entity('IfcBuilding', GlobalId=ifcopenshell.guid.new(), Name='Main building')
    ifcopenshell.api.aggregate.assign_object(model, products=[building], relating_object=project)
    ifcopenshell.api.spatial.assign_container(model, products=[wall], relating_structure=building)
    return model, building, wall


def georeference(model, *, name='EPSG:32633', east=500000., north=1000000., map_prefix=None, scale=1.):
    context = model.by_type('IfcGeometricRepresentationContext', include_subtypes=False)[0]
    unit = model.create_entity('IfcSIUnit', UnitType='LENGTHUNIT', Name='METRE', Prefix=map_prefix)
    crs = model.create_entity('IfcProjectedCRS', Name=name, MapUnit=unit)
    return model.create_entity('IfcMapConversion', SourceCRS=context, TargetCRS=crs,
        Eastings=east, Northings=north, OrthogonalHeight=10., Scale=scale)


def write_export(model, directory):
    source, destination = Path(directory) / 'source.ifc', Path(directory) / 'export.city.json'
    model.write(str(source))
    digest = file_hash(source)
    export_components(source, destination)
    assert file_hash(source) == digest
    return json.loads(destination.read_text()), source, destination


def decoded_vertices(document):
    transform = document['transform']
    return np.array(document['vertices']) * transform['scale'] + transform['translate']


class CityJSONExportTests(unittest.TestCase):
    def test_native_metre_and_millimetre_geometry_have_same_bounds_and_preserve_source(self):
        exports = []
        for millimetres in (False, True):
            with self.subTest(millimetres=millimetres), tempfile.TemporaryDirectory() as directory:
                model, building, wall = with_building(millimetres)
                document, source, _ = write_export(model, directory)
                validate_export(document)
                points = decoded_vertices(document)
                np.testing.assert_allclose(points.min(axis=0), [0., 0., 0.], atol=.0005)
                np.testing.assert_allclose(points.max(axis=0), [5., .2, 4.], atol=.0005)
                self.assertEqual(document['cadevil']['sourceSha256'], file_hash(source))
                self.assertEqual(document['CityObjects'][wall.GlobalId]['parents'], [building.GlobalId])
                self.assertEqual(document['CityObjects'][wall.GlobalId]['type'], 'BuildingConstructiveElement')
                self.assertEqual(document['CityObjects'][wall.GlobalId]['attributes']['ifcGuid'], wall.GlobalId)
                self.assertEqual(document['CityObjects'][wall.GlobalId]['attributes']['ifcMaterials'],
                                 ['Beton bewehrt', 'Bitum. Abdichtung'])
                self.assertNotIn('referenceSystem', document['metadata'])
                self.assertTrue(all(isinstance(value, int) for point in document['vertices'] for value in point))
                exports.append(np.sort(points, axis=0))
        np.testing.assert_allclose(exports[0], exports[1], atol=.0005)

    def test_native_projected_geometry_handles_rotation_wcs_and_mm_scale(self):
        for millimetres in (False, True):
            with self.subTest(millimetres=millimetres), tempfile.TemporaryDirectory() as directory:
                model, _, wall = with_building(millimetres)
                conversion = georeference(model, scale=.001 if millimetres else 1.)
                conversion.XAxisAbscissa = 0.; conversion.XAxisOrdinate = 1.
                document, _, _ = write_export(model, directory)
                self.assertEqual(document['metadata']['referenceSystem'], 'https://www.opengis.net/def/crs/EPSG/0/32633')
                points = decoded_vertices(document)
                np.testing.assert_allclose(points.min(axis=0), [499999.8, 1000000., 10.], atol=.0005)
                np.testing.assert_allclose(points.max(axis=0), [500000., 1000005., 14.], atol=.0005)
        model, _, _ = with_building()
        context = model.by_type('IfcGeometricRepresentationContext', include_subtypes=False)[0]
        context.WorldCoordinateSystem.Location.Coordinates = (10., 20., 0.)
        georeference(model)
        before = model.to_string()
        transform, _, _ = _coordinate_transform(model)
        np.testing.assert_allclose(transform(np.array([[110., 220., 0.]])), [[500100., 1000200., 10.]])
        self.assertEqual(model.to_string(), before)
        wall = model.by_type('IfcWall')[0]
        placement = np.eye(4); placement[:3, 3] = [110., 220., 0.]
        ifcopenshell.api.geometry.edit_object_placement(model, product=wall, matrix=placement, is_si=True)
        with tempfile.TemporaryDirectory() as directory:
            document, _, _ = write_export(model, directory)
        points = decoded_vertices(document)
        np.testing.assert_allclose(points.min(axis=0), [500100., 1000200., 10.], atol=.0005)
        np.testing.assert_allclose(points.max(axis=0), [500105., 1000200.2, 14.], atol=.0005)

    def test_site_latitude_does_not_fabricate_transform_for_exported_geometry(self):
        model, _, _ = with_building()
        model.create_entity('IfcSite', GlobalId=ifcopenshell.guid.new(), RefLatitude=(48, 0, 0), RefLongitude=(16, 0, 0))
        transform, crs, notice = _coordinate_transform(model)
        points = np.array([[16., 48., 0.]])
        np.testing.assert_array_equal(transform(points), points)
        self.assertIsNone(crs)
        self.assertIn('does not define', notice)

    def test_millimetre_quantization_bounds_error_and_omitted_sliver_warning(self):
        model, _, wall = with_building()
        body = wall.Representation.Representations[0].ContextOfItems
        representation = ifcopenshell.api.geometry.add_wall_representation(
            model, context=body, length=5., height=4., thickness=.0001)
        wall.Representation.Representations = [representation]
        placement = np.eye(4); placement[:3, 3] = [.00049, .0001, .00049]
        ifcopenshell.api.geometry.edit_object_placement(model, product=wall, matrix=placement, is_si=True)
        with tempfile.TemporaryDirectory() as directory:
            document, _, _ = write_export(model, directory)
        points = decoded_vertices(document)
        source_bounds = np.array([[.00049, .0001, .00049], [5.00049, .0002, 4.00049]])
        self.assertLessEqual(np.max(np.abs(np.array([points.min(axis=0), points.max(axis=0)]) - source_bounds)), .0005)
        self.assertTrue(any('millimetre precision' in message for message in document['cadevil']['warnings']))
        self.assertGreater(len(document['CityObjects'][wall.GlobalId]['geometry'][0]['boundaries']), 0)

    def test_invalid_ambiguous_and_wrong_unit_georeferencing_is_rejected(self):
        for options in [dict(scale=0.), dict(scale=-1.), dict(name='Local grid'), dict(name='EPSG:4326'), dict(map_prefix='MILLI')]:
            with self.subTest(options=options):
                model, _, _ = with_building()
                georeference(model, **options)
                with self.assertRaises(CityJSONExportError): _coordinate_transform(model)
        model, _, _ = with_building(millimetres=True)
        conversion = georeference(model, scale=.001)
        conversion.TargetCRS.MapUnit = None
        with self.assertRaisesRegex(CityJSONExportError, 'map unit'):
            _coordinate_transform(model)
        model, _, _ = with_building()
        georeference(model); georeference(model)
        with self.assertRaisesRegex(CityJSONExportError, 'multiple'):
            _coordinate_transform(model)

    def test_building_parts_room_furniture_installation_orphan_and_empty_building(self):
        model, building, wall = with_building()
        part = model.create_entity('IfcBuilding', GlobalId=ifcopenshell.guid.new(), Name='Wing', CompositionType='PARTIAL')
        ifcopenshell.api.aggregate.assign_object(model, products=[part], relating_object=building)
        ifcopenshell.api.spatial.assign_container(model, products=[wall], relating_structure=part)
        empty = model.create_entity('IfcBuilding', GlobalId=ifcopenshell.guid.new(), Name='Empty building')
        expected = {'IfcSpace': 'BuildingRoom', 'IfcFurnishingElement': 'BuildingFurniture',
                    'IfcFlowTerminal': 'BuildingInstallation'}
        components = []
        for ifc_type, kind in expected.items():
            component = model.create_entity(ifc_type, GlobalId=ifcopenshell.guid.new(), Name=ifc_type,
                Representation=wall.Representation, ObjectPlacement=wall.ObjectPlacement)
            if component.is_a('IfcSpace'):
                ifcopenshell.api.aggregate.assign_object(model, products=[component], relating_object=part)
            else:
                ifcopenshell.api.spatial.assign_container(model, products=[component], relating_structure=part)
            components.append((component, kind))
        orphan = model.create_entity('IfcWall', GlobalId=ifcopenshell.guid.new(), Name='Orphan wall',
            Representation=wall.Representation, ObjectPlacement=wall.ObjectPlacement)
        opening = model.create_entity('IfcOpeningElement', GlobalId=ifcopenshell.guid.new(), Name='Opening helper',
            Representation=wall.Representation, ObjectPlacement=wall.ObjectPlacement)
        with tempfile.TemporaryDirectory() as directory:
            document, _, _ = write_export(model, directory)
        objects = document['CityObjects']
        self.assertEqual(objects[part.GlobalId]['type'], 'BuildingPart')
        self.assertEqual(objects[part.GlobalId]['parents'], [building.GlobalId])
        self.assertIn(part.GlobalId, objects[building.GlobalId]['children'])
        self.assertEqual(objects[wall.GlobalId]['parents'], [part.GlobalId])
        for component, kind in components:
            self.assertEqual(objects[component.GlobalId]['type'], kind)
            self.assertEqual(objects[component.GlobalId]['parents'], [part.GlobalId])
        self.assertEqual(objects[orphan.GlobalId]['type'], 'OtherConstruction')
        self.assertNotIn('parents', objects[orphan.GlobalId])
        self.assertNotIn(opening.GlobalId, objects)
        self.assertIn(empty.GlobalId, document['cadevil']['emptyBuildings'])
        self.assertTrue(any('empty IFC buildings' in message for message in document['cadevil']['warnings']))
        validate_export(document)
        # Full root oneOf and dispatched official definitions both accept this
        # mixed semantic fixture; geometry remains part of object validation.
        _schema_validators()[0].validate(document)

    def test_repeated_guids_preserve_both_source_components(self):
        model, building, wall = with_building()
        duplicate = model.create_entity('IfcWall', GlobalId=wall.GlobalId, Name='Same GUID second instance',
            Representation=wall.Representation, ObjectPlacement=wall.ObjectPlacement)
        ifcopenshell.api.spatial.assign_container(model, products=[duplicate], relating_structure=building)
        with tempfile.TemporaryDirectory() as directory:
            document, _, _ = write_export(model, directory)
        matches = [(key, obj) for key, obj in document['CityObjects'].items() if obj['attributes']['ifcGuid'] == wall.GlobalId]
        self.assertEqual(len(matches), 2)
        self.assertEqual({obj['attributes']['ifcStepId'] for _, obj in matches}, {wall.id(), duplicate.id()})
        self.assertTrue(all(key in document['CityObjects'][building.GlobalId]['children'] for key, _ in matches))
        self.assertTrue(any('repeated IFC GlobalIds' in message for message in document['cadevil']['warnings']))

    def test_failed_tessellation_stays_visible_in_export_provenance(self):
        model, building, wall = with_building()
        missing = model.create_entity('IfcWall', GlobalId=wall.GlobalId, Name='Missing geometry with repeated GUID',
            Representation=model.create_entity('IfcProductDefinitionShape', Representations=[]))
        ifcopenshell.api.spatial.assign_container(model, products=[missing], relating_structure=building)
        with tempfile.TemporaryDirectory() as directory:
            document, _, _ = write_export(model, directory)
        self.assertIn(missing.GlobalId, document['cadevil']['omittedIfcGuids'])
        self.assertEqual(document['cadevil']['omittedIfcElements'],
                         [{'guid': missing.GlobalId, 'stepId': missing.id(), 'ifcClass': 'IfcWall'}])
        self.assertTrue(any('could not be tessellated' in message for message in document['cadevil']['warnings']))

    def test_schema_and_bidirectional_hierarchy_validation(self):
        model, building, wall = with_building()
        with tempfile.TemporaryDirectory() as directory:
            document, _, _ = write_export(model, directory)
        invalid = copy.deepcopy(document)
        invalid['CityObjects'][wall.GlobalId]['geometry'][0]['boundaries'][0][0][0] = 'not-an-integer'
        from jsonschema import ValidationError
        with self.assertRaises(ValidationError): validate_export(invalid)
        with self.assertRaises(ValidationError): _schema_validators()[0].validate(invalid)
        invalid = copy.deepcopy(document)
        invalid['CityObjects'][wall.GlobalId]['geometry'][0]['boundaries'][0][0][0] = len(document['vertices'])
        with self.assertRaisesRegex(CityJSONExportError, 'vertex reference'): validate_export(invalid)
        invalid = copy.deepcopy(document)
        invalid['CityObjects'][building.GlobalId]['children'].append('missing-guid')
        with self.assertRaisesRegex(CityJSONExportError, 'hierarchy'): validate_export(invalid)
        invalid = copy.deepcopy(document)
        invalid['CityObjects'][building.GlobalId]['parents'] = [wall.GlobalId]
        invalid['CityObjects'][wall.GlobalId]['children'] = [building.GlobalId]
        with self.assertRaisesRegex(CityJSONExportError, 'cycle'): validate_export(invalid)

    def test_cache_uses_native_worker_and_does_not_modify_ifc(self):
        model, _, _ = with_building()
        with tempfile.TemporaryDirectory() as directory:
            source, cache = Path(directory) / 'source.ifc', Path(directory) / 'cache'
            model.write(str(source)); digest = file_hash(source)
            result = model_cityjson(source, cache)
            self.assertTrue(result.exists()); self.assertEqual(file_hash(source), digest)
            document = json.loads(result.read_text())
            validate_export(document)
            self.assertEqual(document['metadata']['title'], 'Passport test')
            second = Path(directory) / 'another-owners-filename.ifc'
            second.write_bytes(source.read_bytes())
            with patch('apps.shared.cityjson_export.subprocess.run', side_effect=AssertionError('cache miss')):
                self.assertEqual(model_cityjson(source, cache), result)
                self.assertEqual(model_cityjson(second, cache), result)

    def test_changed_source_never_publishes_an_export(self):
        model, _, _ = with_building()
        with tempfile.TemporaryDirectory() as directory:
            source, destination = Path(directory) / 'source.ifc', Path(directory) / 'export.city.json'
            model.write(str(source))
            with patch('apps.shared.cityjson_export.file_hash', side_effect=['a' * 64, 'b' * 64]):
                with self.assertRaisesRegex(CityJSONExportError, 'source changed'):
                    export_components(source, destination)
            self.assertFalse(destination.exists())
