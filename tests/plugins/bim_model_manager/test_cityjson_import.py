"""Native conversion regressions and bounded upload preflight tests."""
import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element
import ifcopenshell.validate
from pyproj import Transformer

from plugins.bim_model_manager.cityjson_import import CityJSONImportError, CityJSONLimits, convert_cityjson, inspect_cityjson


def cube():
    faces = [[[0, 3, 2, 1]], [[4, 5, 6, 7]], [[0, 1, 5, 4]],
             [[1, 2, 6, 5]], [[2, 3, 7, 6]], [[3, 0, 4, 7]]]
    return {'type': 'CityJSON', 'version': '2.0',
        'metadata': {'referenceSystem': 'https://www.opengis.net/def/crs/EPSG/0/32633'},
        'transform': {'scale': [.1, .1, .1], 'translate': [500000, 5300000, 100]},
        'vertices': [[0, 0, 0], [100, 0, 0], [100, 100, 0], [0, 100, 0],
                     [0, 0, 100], [100, 0, 100], [100, 100, 100], [0, 100, 100]],
        'CityObjects': {'source-building': {'type': 'Building',
            'attributes': {'name': 'House', 'year': 2024, 'active': True,
                           'details': {'address': ['Example', 12]}, 'missing': None},
            'geometry': [{'type': 'Solid', 'lod': '1', 'boundaries': [faces]}]}}}


def points(model):
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    found = []
    for product in model.by_type('IfcProduct'):
        if product.Representation:
            shape = ifcopenshell.geom.create_shape(settings, product)
            geometry = shape.geometry
            found.extend(zip(*(iter(geometry.verts),) * 3))
    return found


def extrema(values):
    return tuple((min(v[i] for v in values), max(v[i] for v in values)) for i in range(3))


class CityJSONImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.source = self.folder / 'source.city.json'
        self.output = self.folder / 'result.ifc'

    def tearDown(self):
        self.temp.cleanup()

    def write(self, data):
        self.source.write_text(json.dumps(data, ensure_ascii=False))
        return self.source.read_bytes()

    def convert(self, data=None, **kwargs):
        self.write(data or cube())
        return convert_cityjson(self.source, self.output, lod=kwargs.pop('lod', '1'), **kwargs)

    def test_transformed_projected_geometry_and_source_properties(self):
        data = cube(); original = self.write(data)
        report = convert_cityjson(self.source, self.output, lod='1.0', name_attribute='name')
        model = ifcopenshell.open(self.output)
        self.assertEqual(extrema(points(model)), ((0.0, 10.0), (0.0, 10.0), (0.0, 10.0)))
        conversion = model.by_type('IfcMapConversion')[0]
        self.assertEqual((conversion.Eastings, conversion.Northings, conversion.OrthogonalHeight), (500000, 5300000, 100))
        self.assertEqual(conversion.TargetCRS.Name, 'EPSG:32633')
        self.assertEqual(report['lod'], '1')
        self.assertEqual(report['source_sha256'], hashlib.sha256(original).hexdigest())
        self.assertEqual(report['output_sha256'], hashlib.sha256(self.output.read_bytes()).hexdigest())
        building = model.by_type('IfcBuilding')[0]
        self.assertEqual(building.Name, 'House')
        props = ifcopenshell.util.element.get_psets(building)
        self.assertEqual(json.loads(props['CadevilCityJSON']['SourceAttributesJSON']), data['CityObjects']['source-building']['attributes'])
        self.assertEqual(props['CadevilCityJSON']['SourceCityObjectId'], 'source-building')
        self.assertEqual(props['CityJSON_attributes']['details'], json.dumps(data['CityObjects']['source-building']['attributes']['details']))
        self.assertEqual(props['CityJSON_attributes']['missing'], 'null')
        self.assertEqual(self.source.read_bytes(), original)
        location = report['buildings'][0]
        longitude, latitude = Transformer.from_crs(32633, 4326, always_xy=True).transform(500005, 5300005)
        self.assertEqual(location['guid'], building.GlobalId)
        self.assertEqual(location['source_id'], 'source-building')
        self.assertEqual(location['representative_point'], [500005, 5300005, 105])
        self.assertAlmostEqual(location['latitude'], latitude)
        self.assertAlmostEqual(location['longitude'], longitude)

    def test_untransformed_projected_coordinates_use_explicit_origin(self):
        data = cube(); transform = data.pop('transform')
        data['vertices'] = [[v[i] * transform['scale'][i] + transform['translate'][i] for i in range(3)] for v in data['vertices']]
        self.convert(data)
        model = ifcopenshell.open(self.output)
        self.assertEqual(extrema(points(model)), ((0.0, 10.0), (0.0, 10.0), (0.0, 10.0)))
        mapping = model.by_type('IfcMapConversion')[0]
        self.assertEqual((mapping.Eastings, mapping.Northings, mapping.OrthogonalHeight), (500000, 5300000, 100))

    def test_without_crs_transform_is_retained_without_guessing_location(self):
        data = cube(); data.pop('metadata')
        data['transform']['translate'] = [20, 30, 40]
        report = self.convert(data)
        model = ifcopenshell.open(self.output)
        self.assertEqual(extrema(points(model)), ((20.0, 30.0), (30.0, 40.0), (40.0, 50.0)))
        self.assertEqual(model.by_type('IfcMapConversion'), [])
        self.assertEqual(report['buildings'][0]['status'], 'unavailable')
        self.assertIsNone(report['buildings'][0]['latitude'])

    def test_explicit_lod_prunes_unsupported_alternate_geometry(self):
        data = cube(); data['CityObjects']['source-building']['geometry'].append(
            {'type': 'GeometryInstance', 'lod': '2', 'template': 0, 'boundaries': [0]})
        self.write(data)
        inspection = inspect_cityjson(self.source)
        self.assertEqual(inspection['lods'], ['1', '2'])
        self.assertEqual(inspection['supported_lods'], ['1'])
        self.assertEqual(inspection['default_lod'], '1')
        report = convert_cityjson(self.source, self.output, lod='1')
        self.assertEqual(report['geometry_products'], 1)
        self.assertEqual([c.UserDefinedTargetView for c in ifcopenshell.open(self.output).by_type('IfcGeometricRepresentationSubContext')], ['LOD1'])
        with patch('plugins.bim_model_manager.cityjson_import.subprocess.run') as worker:
            with self.assertRaisesRegex(CityJSONImportError, 'GeometryInstance'):
                convert_cityjson(self.source, self.output, lod='2')
            worker.assert_not_called()

    def test_distinct_selected_lod_geometry_is_used_once(self):
        data = cube(); data['vertices'] += [[0, 0, 200], [100, 0, 200], [100, 100, 200], [0, 100, 200]]
        geometry = copy.deepcopy(data['CityObjects']['source-building']['geometry'][0]); geometry['lod'] = '2.2'
        geometry['boundaries'] = [[[[index + 4 if index >= 4 else index for index in face[0]]] for face in geometry['boundaries'][0]]]
        data['CityObjects']['source-building']['geometry'].append(geometry)
        report = self.convert(data, lod='2.2')
        self.assertEqual(extrema(points(ifcopenshell.open(self.output)))[2], (0.0, 20.0))
        self.assertEqual(report['selected_faces'], 6)
        self.assertEqual(report['buildings'][0]['representative_point'][2], 110)

    def test_nested_solid_semantics_preserve_unclassified_faces(self):
        data = cube(); geometry = data['CityObjects']['source-building']['geometry'][0]
        geometry['semantics'] = {'surfaces': [{'type': 'GroundSurface'}, {'type': 'RoofSurface'}, {'type': 'WallSurface'}],
                                 'values': [[0, 1, 2, 2, 2, None]]}
        report = self.convert(data)
        model = ifcopenshell.open(self.output)
        self.assertEqual(len(model.by_type('IfcFace')), 6)
        self.assertEqual(len(model.by_type('IfcWall')), 1)
        self.assertEqual(len(model.by_type('IfcRoof')), 1)
        self.assertEqual(len(model.by_type('IfcBuildingElementProxy')), 1)
        self.assertEqual(report['geometry_products'], 4)
        self.assertEqual(extrema(points(model)), ((0.0, 10.0), (0.0, 10.0), (0.0, 10.0)))

    def test_converted_semantic_ifc_obeys_express_rules(self):
        data = cube(); data['CityObjects']['source-building']['geometry'][0]['semantics'] = {
            'surfaces': [{'type': 'WallSurface'}], 'values': [[0] * 6]}
        self.convert(data)
        logger = ifcopenshell.validate.json_logger()
        ifcopenshell.validate.validate(ifcopenshell.open(self.output), logger, express_rules=True)
        self.assertEqual(logger.statements, [])

    def test_nonspatial_semantic_objects_use_valid_decomposition(self):
        data = cube(); obj = data['CityObjects']['source-building']; obj['type'] = 'Road'
        obj['geometry'][0]['semantics'] = {'surfaces': [{'type': 'TrafficArea'}], 'values': [[0] * 6]}
        report = self.convert(data)
        model = ifcopenshell.open(self.output)
        self.assertEqual(model.by_type('IfcRelContainedInSpatialStructure'), [])
        self.assertEqual(report['buildings'], [])
        self.assertTrue(points(model))
        logger = ifcopenshell.validate.json_logger()
        ifcopenshell.validate.validate(model, logger, express_rules=True)
        self.assertEqual(logger.statements, [])

    def test_parent_bounds_and_source_ids_survive_duplicate_names(self):
        data = cube(); part = data['CityObjects'].pop('source-building'); part['type'] = 'BuildingPart'
        part['attributes']['name'] = 'Same name'; part['parents'] = ['parent']
        data['CityObjects'] = {'parent': {'type': 'Building', 'attributes': {'name': 'Same name'}, 'children': ['child']}, 'child': part}
        report = self.convert(data, name_attribute='name')
        self.assertEqual({r['source_id'] for r in report['buildings']}, {'parent', 'child'})
        self.assertEqual(len({r['guid'] for r in report['buildings']}), 2)
        self.assertTrue(all(r['status'] == 'located' for r in report['buildings']))
        self.assertTrue(all(r['representative_point'] == [500005, 5300005, 105] for r in report['buildings']))
        model = ifcopenshell.open(self.output)
        mapped = {ifcopenshell.util.element.get_psets(b)['CadevilCityJSON']['SourceCityObjectId']: b for b in model.by_type('IfcBuilding')}
        self.assertEqual({r['source_id']: r['guid'] for r in report['buildings']}, {key: b.GlobalId for key, b in mapped.items()})
        self.assertEqual(mapped['child'].Decomposes[0].RelatingObject, mapped['parent'])

    def test_inner_shells_rejected_before_worker(self):
        for kind in ('Solid', 'MultiSolid'):
            with self.subTest(kind=kind):
                data = cube(); geometry = data['CityObjects']['source-building']['geometry'][0]
                shell = geometry['boundaries'][0]
                geometry['type'] = kind; geometry['boundaries'] = [shell, shell] if kind == 'Solid' else [[shell, shell]]
                self.write(data)
                with patch('plugins.bim_model_manager.cityjson_import.subprocess.run') as worker:
                    with self.assertRaisesRegex(CityJSONImportError, 'interior shell'):
                        convert_cityjson(self.source, self.output, lod='1')
                    worker.assert_not_called()

    def test_bad_references_semantics_and_limits_rejected(self):
        for mutator, message in (
            (lambda d: d['CityObjects']['source-building']['geometry'][0]['boundaries'][0][0][0].append(99), 'vertex indices'),
            (lambda d: d['CityObjects']['source-building']['geometry'][0].update(semantics={'surfaces': [{'type': 'WallSurface'}], 'values': [[2] * 6]}), 'Semantic values'),
            (lambda d: d['CityObjects']['source-building']['geometry'][0].update(semantics={'surfaces': [{'type': 'WallSurface'}], 'values': [[0] * 7]}), 'Semantic values'),
            (lambda d: d['CityObjects']['source-building']['geometry'][0].update(boundaries=[[]]), 'no polygon faces'),
            (lambda d: d['CityObjects']['source-building'].update(parents=['missing']), 'existing CityObject'),
            (lambda d: d['CityObjects']['source-building'].update(children=['source-building']), 'cycle')):
            with self.subTest(message=message):
                data = cube(); mutator(data); self.write(data)
                with self.assertRaisesRegex(CityJSONImportError, message):
                    convert_cityjson(self.source, self.output, lod='1')
        self.write(cube())
        for limits, message in ((replace(CityJSONLimits(), max_faces=5), 'face import limit'),
                                (replace(CityJSONLimits(), max_vertices=7), 'vertices'),
                                (replace(CityJSONLimits(), max_boundary_references=3), 'boundary-reference'),
                                (replace(CityJSONLimits(), max_bytes=10), 'file exceeds')):
            with self.subTest(message=message):
                with self.assertRaisesRegex(CityJSONImportError, message):
                    convert_cityjson(self.source, self.output, lod='1', limits=limits)

    def test_crs_must_be_known_projected_metres(self):
        for code in ('4326', '2263', '99999999'):
            data = cube(); data['metadata']['referenceSystem'] = f'https://www.opengis.net/def/crs/EPSG/0/{code}'
            self.write(data)
            with self.subTest(code=code), self.assertRaises(CityJSONImportError):
                inspect_cityjson(self.source)

    def test_json_duplicates_nonfinite_and_depth_are_friendly(self):
        for raw, message in (('{"type":"CityJSON","type":"CityJSON"}', 'duplicate keys'),
                             ('{"x":NaN}', 'finite JSON numbers'),
                             ('{"x":1e1000}', 'finite JSON numbers'),
                             ('{"x":[' * 40 + '1' + ']}' * 40, 'nesting limit'),
                             ('{oops}', 'line 1')):
            with self.subTest(message=message):
                self.source.write_text(raw)
                with self.assertRaisesRegex(CityJSONImportError, message):
                    inspect_cityjson(self.source)

    def test_numeric_overflow_is_rejected_before_native_conversion(self):
        for mutate in (lambda d: d['vertices'][0].__setitem__(0, 10 ** 1000),
                       lambda d: d['transform'].__setitem__('scale', [1e308] * 3)):
            data = cube(); mutate(data); self.write(data)
            with patch('plugins.bim_model_manager.cityjson_import.subprocess.run') as worker:
                with self.assertRaises(CityJSONImportError):
                    convert_cityjson(self.source, self.output, lod='1')
                worker.assert_not_called()

    def test_precise_map_projection_failure_leaves_import_usable(self):
        from plugins.bim_model_manager.cityjson_import import _building_records
        from pyproj.exceptions import ProjError
        self.convert()
        model = ifcopenshell.open(self.output)
        building = model.by_type('IfcBuilding')[0]
        with patch('pyproj.Transformer.from_crs', side_effect=ProjError('grid unavailable')):
            rows = _building_records(cube(), '1', {'source-building': building.GlobalId}, model)
        self.assertEqual(rows[0]['status'], 'unavailable')
        self.assertIn('precise offline', rows[0]['message'])
        self.assertIsNone(rows[0]['latitude'])

    def test_timeout_preserves_existing_destination_and_cleans_job(self):
        original = self.write(cube()); self.output.write_bytes(b'existing artifact')
        with patch('plugins.bim_model_manager.cityjson_import.subprocess.run', side_effect=subprocess.TimeoutExpired('worker', 1)):
            with self.assertRaisesRegex(CityJSONImportError, 'time limit'):
                convert_cityjson(self.source, self.output, lod='1', timeout_seconds=1)
        self.assertEqual(self.output.read_bytes(), b'existing artifact')
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(list(self.folder.glob('cadevil-cityjson-*')), [])

    def test_original_source_cannot_be_used_as_destination(self):
        original = self.write(cube())
        with self.assertRaisesRegex(CityJSONImportError, 'separate path'):
            convert_cityjson(self.source, self.source, lod='1')
        self.assertEqual(self.source.read_bytes(), original)

    def test_worker_failure_is_bounded_and_does_not_publish(self):
        self.write(cube()); self.output.write_bytes(b'old')
        failed = subprocess.CompletedProcess('worker', 1, json.dumps({'ok': False, 'message': 'repair ' + 'x' * 1000}).encode(), b'native details')
        with patch('plugins.bim_model_manager.cityjson_import.subprocess.run', return_value=failed):
            with self.assertRaises(CityJSONImportError) as error:
                convert_cityjson(self.source, self.output, lod='1')
        self.assertLessEqual(len(str(error.exception)), 500)
        self.assertEqual(self.output.read_bytes(), b'old')


if __name__ == '__main__':
    unittest.main()
