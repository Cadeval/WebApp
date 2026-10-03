import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ifcopenshell

from .building_locations import extract_building_locations, source_building_locations


def model_with_building(*, latitude=None, longitude=None, placement=(0., 0., 0.), prefix=None, wcs=(0., 0., 0.)):
    model = ifcopenshell.file(schema='IFC4')
    length = model.create_entity('IfcSIUnit', UnitType='LENGTHUNIT', Name='METRE', Prefix=prefix)
    axis = model.create_entity('IfcAxis2Placement3D', Location=model.create_entity('IfcCartesianPoint', Coordinates=wcs))
    context = model.create_entity('IfcGeometricRepresentationContext', ContextType='Model',
                                 CoordinateSpaceDimension=3, Precision=1e-6, WorldCoordinateSystem=axis)
    project = model.create_entity('IfcProject', GlobalId=ifcopenshell.guid.new(), Name='Map test',
                                 UnitsInContext=model.create_entity('IfcUnitAssignment', Units=[length]),
                                 RepresentationContexts=[context])
    site = model.create_entity('IfcSite', GlobalId=ifcopenshell.guid.new(), Name='Declared site',
                               RefLatitude=latitude, RefLongitude=longitude)
    local = None if placement is None else model.create_entity('IfcLocalPlacement',
        RelativePlacement=model.create_entity('IfcAxis2Placement3D',
            Location=model.create_entity('IfcCartesianPoint', Coordinates=placement)))
    building = model.create_entity('IfcBuilding', GlobalId=ifcopenshell.guid.new(), Name='Building', ObjectPlacement=local)
    model.create_entity('IfcRelAggregates', GlobalId=ifcopenshell.guid.new(), RelatingObject=project, RelatedObjects=[site])
    model.create_entity('IfcRelAggregates', GlobalId=ifcopenshell.guid.new(), RelatingObject=site, RelatedObjects=[building])
    return model, building, site, context


def map_conversion(model, context, *, name='EPSG:32633', east=500000., north=0., scale=None, map_prefix=None):
    unit = model.create_entity('IfcSIUnit', UnitType='LENGTHUNIT', Name='METRE', Prefix=map_prefix)
    crs = model.create_entity('IfcProjectedCRS', Name=name, MapUnit=unit)
    return model.create_entity('IfcMapConversion', SourceCRS=context, TargetCRS=crs,
                               Eastings=east, Northings=north, OrthogonalHeight=0., Scale=scale)


class BuildingLocationTests(unittest.TestCase):
    def test_site_microseconds_and_negative_angles_keep_declared_origin(self):
        model, _, _, _ = model_with_building(latitude=(48, 10, 44, 600000), longitude=(16, 23, 10, 200000))
        row = extract_building_locations(model)[0]
        self.assertEqual((row['status'], row['source'], row['site_name'], row['crs']),
                         ('located', 'ifc_site', 'Declared site', 'EPSG:4326'))
        self.assertAlmostEqual(row['latitude'], 48 + 10/60 + 44.6/3600)
        self.assertAlmostEqual(row['longitude'], 16 + 23/60 + 10.2/3600)
        self.assertIn('not the building centroid', row['message'])
        model.by_type('IfcSite')[0].RefLatitude = (-33, -30, 0)
        self.assertAlmostEqual(extract_building_locations(model)[0]['latitude'], -33.5)

    def test_equator_and_prime_meridian_are_valid_and_no_local_coordinates_are_inferred(self):
        model, _, site, _ = model_with_building(latitude=(0, 0, 0), longitude=(0, 0, 0), placement=(16., 48., 0.))
        row = extract_building_locations(model)[0]
        self.assertEqual((row['status'], row['latitude'], row['longitude']), ('located', 0., 0.))
        site.RefLatitude = site.RefLongitude = None
        row = extract_building_locations(model)[0]
        self.assertEqual(row['status'], 'missing')
        self.assertIsNone(row['latitude']); self.assertIsNone(row['longitude'])

    def test_partial_mixed_sign_and_out_of_range_site_angles_are_invalid(self):
        for latitude, longitude in [((48, 0, 0), None), ((-33, 30, 0), (16, 0, 0)),
                                     ((48, 60, 0), (16, 0, 0)), ((91, 0, 0), (16, 0, 0)),
                                     ((48, 0, 0), (181, 0, 0))]:
            with self.subTest(latitude=latitude, longitude=longitude):
                model, _, _, _ = model_with_building(latitude=latitude, longitude=longitude)
                row = extract_building_locations(model)[0]
                self.assertEqual(row['status'], 'invalid')
                self.assertIsNone(row['latitude']); self.assertIsNone(row['longitude'])

    def test_distinct_buildings_share_site_reference_without_invented_offsets(self):
        model, building, site, _ = model_with_building(latitude=(48, 0, 0), longitude=(16, 0, 0))
        second = model.create_entity('IfcBuilding', GlobalId=ifcopenshell.guid.new(), Name='Another building')
        relation = building.Decomposes[0]
        relation.RelatedObjects = [building, second]
        rows = extract_building_locations(model)
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]['guid'], rows[1]['guid'])
        self.assertEqual([(r['latitude'], r['longitude']) for r in rows], [(48., 16.), (48., 16.)])

    def test_unrelated_site_does_not_supply_a_building_location(self):
        model, building, _, _ = model_with_building(latitude=(48, 0, 0), longitude=(16, 0, 0))
        model.remove(building.Decomposes[0])
        self.assertEqual(extract_building_locations(model)[0]['status'], 'missing')

    def test_duplicate_building_guids_are_invalid(self):
        model, building, _, _ = model_with_building(latitude=(48, 0, 0), longitude=(16, 0, 0))
        model.create_entity('IfcBuilding', GlobalId=building.GlobalId, Name='Duplicate')
        self.assertEqual([r['status'] for r in extract_building_locations(model)], ['invalid', 'invalid'])

    def test_projected_building_anchor_uses_nested_placement_axis_and_wcs(self):
        model, building, _, context = model_with_building(placement=(110., 220., 0.), wcs=(10., 20., 0.))
        conversion = map_conversion(model, context)
        conversion.XAxisAbscissa = 0.; conversion.XAxisOrdinate = 1.
        row = extract_building_locations(model)[0]
        from pyproj import Transformer
        longitude, latitude = Transformer.from_crs(32633, 4326, always_xy=True).transform(499800., 100.)
        self.assertEqual((row['status'], row['source'], row['crs']), ('located', 'projected_crs', 'EPSG:32633'))
        self.assertAlmostEqual(row['latitude'], latitude, places=8)
        self.assertAlmostEqual(row['longitude'], longitude, places=8)
        parent = model.create_entity('IfcLocalPlacement', RelativePlacement=model.create_entity('IfcAxis2Placement3D',
            Location=model.create_entity('IfcCartesianPoint', Coordinates=(100., 200., 0.))))
        building.ObjectPlacement.PlacementRelTo = parent
        building.ObjectPlacement.RelativePlacement.Location.Coordinates = (10., 20., 0.)
        self.assertEqual(extract_building_locations(model)[0], row)

    def test_projected_project_and_map_units_are_resolved_explicitly(self):
        model, _, _, context = model_with_building(prefix='MILLI', placement=(100000., 200000., 0.))
        conversion = map_conversion(model, context, scale=.001)
        from pyproj import Transformer
        longitude, latitude = Transformer.from_crs(32633, 4326, always_xy=True).transform(500100., 200.)
        row = extract_building_locations(model)[0]
        self.assertEqual(row['status'], 'located')
        self.assertAlmostEqual(row['latitude'], latitude, places=8)
        self.assertAlmostEqual(row['longitude'], longitude, places=8)
        conversion.Scale = None
        self.assertEqual(extract_building_locations(model)[0]['status'], 'invalid')
        model, _, _, context = model_with_building(prefix='MILLI')
        map_conversion(model, context, east=500000000., map_prefix='MILLI')
        row = extract_building_locations(model)[0]
        self.assertEqual(row['status'], 'located')
        self.assertAlmostEqual(row['latitude'], 0.)
        self.assertAlmostEqual(row['longitude'], 15.)

    def test_invalid_or_ambiguous_conversion_never_falls_back_to_site(self):
        model, _, _, context = model_with_building(latitude=(48, 0, 0), longitude=(16, 0, 0))
        conversion = map_conversion(model, context, scale=0.)
        row = extract_building_locations(model)[0]
        self.assertEqual(row['status'], 'invalid'); self.assertIsNone(row['latitude'])
        conversion.Scale = 1.
        map_conversion(model, context)
        row = extract_building_locations(model)[0]
        self.assertEqual(row['status'], 'unsupported'); self.assertIsNone(row['longitude'])

    def test_crs_and_missing_placement_are_explicitly_unsupported(self):
        for name, placement in [('Local survey grid', (0., 0., 0.)), ('/tmp/unsafe.proj', (0., 0., 0.)),
                                ('EPSG:4326', (0., 0., 0.)), ('EPSG:32633', None)]:
            with self.subTest(name=name, placement=placement):
                model, _, _, context = model_with_building(placement=placement)
                map_conversion(model, context, name=name)
                row = extract_building_locations(model)[0]
                self.assertEqual(row['status'], 'unsupported')
                self.assertIsNone(row['latitude']); self.assertIsNone(row['longitude'])

    def test_source_cache_preserves_ifc_and_invalidates_by_content(self):
        model, building, _, _ = model_with_building(latitude=(48, 0, 0), longitude=(16, 0, 0))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.ifc'; cache = Path(directory) / 'cache'
            model.write(str(path)); before = path.read_bytes()
            data = source_building_locations(path, cache)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(data['buildings'][0]['status'], 'located')
            with patch('apps.shared.building_locations.ifcopenshell.open', side_effect=AssertionError('cache miss')):
                self.assertEqual(source_building_locations(path, cache), data)
            building.Name = 'Revised'; model.write(str(path))
            new = source_building_locations(path, cache)
            self.assertNotEqual(new['ifc_sha256'], data['ifc_sha256'])
            self.assertEqual(new['buildings'][0]['name'], 'Revised')
            self.assertEqual(len(list(cache.glob('*.json'))), 2)

    def test_changed_source_during_extraction_is_rejected(self):
        model, _, _, _ = model_with_building()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.ifc'; model.write(str(path))
            with patch('apps.shared.building_locations.file_hash', side_effect=['a' * 64, 'b' * 64]):
                with self.assertRaisesRegex(ValueError, 'source changed'):
                    source_building_locations(path)

    @unittest.skipUnless(os.environ.get('CADEVIL_REAL_IFC_DIR'), 'Set CADEVIL_REAL_IFC_DIR for all A–D source models.')
    def test_actual_a_to_d_models_have_declared_site_references(self):
        expected = [('Modell_A 28V_new.ifc', 48 + 10/60 + 44.6/3600, 16 + 23/60 + 10.2/3600),
                    ('Modell_B 28V_new.ifc', 48 + 13/60, 16 + 22/60),
                    ('Modell_C_28V_new.ifc', 48 + 13/60, 16 + 22/60),
                    ('Modell_D_28V_new.ifc', 48 + 13/60, 16 + 22/60)]
        evidence = []
        for name, latitude, longitude in expected:
            with self.subTest(name=name):
                path = Path(os.environ['CADEVIL_REAL_IFC_DIR']) / name
                data = source_building_locations(path)
                self.assertEqual(len(data['buildings']), 1)
                row = data['buildings'][0]
                self.assertEqual((row['status'], row['source']), ('located', 'ifc_site'))
                self.assertAlmostEqual(row['latitude'], latitude, places=10)
                self.assertAlmostEqual(row['longitude'], longitude, places=10)
                evidence.append({'filename': name, **data})
        if destination := os.environ.get('CADEVIL_LOCATION_EVIDENCE'):
            Path(destination).write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
