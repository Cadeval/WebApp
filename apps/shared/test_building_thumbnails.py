import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import ifcopenshell
import numpy as np
from PIL import Image

from apps.shared import building_thumbnails as thumbnails
from apps.shared.ifc_extractor import test_material_assessment as fixtures


RED = '0AAAAAAAAAAAAAAAAAAAAA'
BLUE = '0BBBBBBBBBBBBBBBBBBBBB'


def write_glb(path, *, helpers=False, transform=None):
    vertices = np.asarray([[-1,-1,-1], [1,-1,-1], [1,1,-1], [-1,1,-1],
                           [-1,-1,1], [1,-1,1], [1,1,1], [-1,1,1]], dtype='<f4')
    indices = np.asarray([0,2,1,0,3,2,4,5,6,4,6,7,0,1,5,0,5,4,2,3,7,2,7,6,0,4,7,0,7,3,1,2,6,1,6,5], dtype='<u2')
    binary = vertices.tobytes() + indices.tobytes()
    binary += b'\0' * (-len(binary) % 4)
    nodes = [{'mesh':0,'translation':[-5,0,0],'extras':{'GlobalId':RED,'ifcType':'IfcWall'}},
             {'mesh':1,'translation':[5,0,0],'extras':{'GlobalId':BLUE,'ifcType':'IfcWall'}}]
    if helpers:
        nodes.append({'mesh':0,'scale':[100,100,100],'extras':{'GlobalId':'0CCCCCCCCCCCCCCCCCCCCC','ifcType':'IfcSpace'}})
        nodes.append({'mesh':1,'scale':[200,200,200],'extras':{'GlobalId':'0DDDDDDDDDDDDDDDDDDDDD','ifcType':'IfcOpeningElement'}})
    roots = list(range(len(nodes)))
    if transform:
        nodes.append({**transform,'children':roots}); roots = [len(nodes)-1]
    document = {'asset':{'version':'2.0'},'scene':0,'scenes':[{'nodes':roots}], 'nodes':nodes,
        'buffers':[{'byteLength':len(binary)}],
        'bufferViews':[{'buffer':0,'byteOffset':0,'byteLength':vertices.nbytes},{'buffer':0,'byteOffset':vertices.nbytes,'byteLength':indices.nbytes}],
        'accessors':[{'bufferView':0,'componentType':5126,'count':len(vertices),'type':'VEC3'}, {'bufferView':1,'componentType':5123,'count':len(indices),'type':'SCALAR'}],
        'materials':[{'doubleSided':True,'pbrMetallicRoughness':{'baseColorFactor':[.8,.03,.03,1]}}, {'doubleSided':True,'pbrMetallicRoughness':{'baseColorFactor':[.03,.03,.8,1]}}],
        'meshes':[{'primitives':[{'attributes':{'POSITION':0},'indices':1,'material':index}]} for index in range(2)]}
    encoded = json.dumps(document,separators=(',',':')).encode(); encoded += b' ' * (-len(encoded) % 4)
    path.write_bytes(struct.pack('<4sII',b'glTF',2,28+len(encoded)+len(binary)) + struct.pack('<I4s',len(encoded),b'JSON') + encoded + struct.pack('<I4s',len(binary),b'BIN\0') + binary)
    return document


class BuildingThumbnailTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.glb = self.root / 'model.glb'; write_glb(self.glb)

    def render(self, name='preview.png', **kwargs):
        destination = self.root / name
        thumbnails.render_glb_thumbnail(self.glb, destination, **kwargs)
        with Image.open(destination) as image:
            self.assertEqual(image.format,'PNG'); self.assertEqual(image.size,thumbnails.SIZE)
            pixels = np.asarray(image).astype(int)
        return destination, pixels

    def test_actual_declared_colours_survive_lighting_and_output_is_deterministic(self):
        first, pixels = self.render()
        self.assertGreater(np.count_nonzero(pixels[:,:,0] > pixels[:,:,2] + 30),100)
        self.assertGreater(np.count_nonzero(pixels[:,:,2] > pixels[:,:,0] + 30),100)
        second, _ = self.render('second.png')
        self.assertEqual(first.read_bytes(),second.read_bytes())
        self.assertEqual(first.stat().st_mode & 0o777,0o600)

    def test_selected_building_products_exclude_other_geometry_and_fit_selection(self):
        _, pixels = self.render(product_guids={RED})
        self.assertGreater(np.count_nonzero(pixels[:,:,0] > pixels[:,:,2] + 30),1000)
        self.assertEqual(np.count_nonzero(pixels[:,:,2] > pixels[:,:,0] + 30),0)
        with self.assertRaisesRegex(thumbnails.ThumbnailError,'No renderable'):
            self.render(product_guids={'missing'})

    def test_space_and_opening_display_volumes_do_not_obscure_or_resize_actual_building(self):
        original, _ = self.render()
        before = original.read_bytes()
        write_glb(self.glb, helpers=True)
        actual, _ = self.render('with-helpers.png')
        self.assertEqual(actual.read_bytes(),before)

    def test_node_placements_apply_to_actual_vertices_without_modifying_source(self):
        write_glb(self.glb,transform={'translation':[1_000_000,20,500_000],'scale':[2,3,4]})
        before = self.glb.read_bytes()
        document, binary = thumbnails._read_glb(self.glb)
        triangles, _, _ = thumbnails._triangles(document,binary,{RED})
        np.testing.assert_allclose(triangles.min(axis=(0,1)),[999988,17,499996])
        np.testing.assert_allclose(triangles.max(axis=(0,1)),[999992,23,500004])
        self.render(product_guids={RED})
        self.assertEqual(self.glb.read_bytes(),before)

    def test_cache_hits_reuse_geometry_and_corrupt_png_is_regenerated(self):
        source = self.root / 'source.ifc'; source.write_text('cached source fingerprint fixture')
        cache = self.root / 'cache'
        with patch.object(thumbnails,'model_glb',return_value=self.glb) as geometry:
            first = thumbnails.model_thumbnail(source,cache)
            self.assertEqual(thumbnails.model_thumbnail(source,cache),first)
            self.assertEqual(geometry.call_count,1)
            first.write_bytes(b'corrupt png')
            thumbnails.model_thumbnail(source,cache)
            self.assertEqual(geometry.call_count,2)
            source.write_text('changed source fingerprint fixture')
            second = thumbnails.model_thumbnail(source,cache)
            self.assertNotEqual(first,second)
            self.assertEqual(cache.stat().st_mode & 0o777,0o700)

    def test_two_actual_ifc_buildings_use_only_their_spatially_contained_products(self):
        model = ifcopenshell.file(schema='IFC4')
        building_a = model.create_entity('IfcBuilding',GlobalId=ifcopenshell.guid.new(),Name='A')
        building_b = model.create_entity('IfcBuilding',GlobalId=ifcopenshell.guid.new(),Name='B')
        storey = model.create_entity('IfcBuildingStorey',GlobalId=ifcopenshell.guid.new())
        red = model.create_entity('IfcWall',GlobalId=RED); blue = model.create_entity('IfcWall',GlobalId=BLUE)
        model.create_entity('IfcRelAggregates',GlobalId=ifcopenshell.guid.new(),RelatingObject=building_a,RelatedObjects=[storey])
        model.create_entity('IfcRelContainedInSpatialStructure',GlobalId=ifcopenshell.guid.new(),RelatingStructure=storey,RelatedElements=[red])
        model.create_entity('IfcRelContainedInSpatialStructure',GlobalId=ifcopenshell.guid.new(),RelatingStructure=building_b,RelatedElements=[blue])
        source = self.root / 'two-buildings.ifc'; model.write(str(source)); before=source.read_bytes()
        with patch.object(thumbnails,'model_glb',return_value=self.glb):
            a=thumbnails.model_thumbnail(source,self.root/'cache',building_guid=building_a.GlobalId)
            b=thumbnails.model_thumbnail(source,self.root/'cache',building_guid=building_b.GlobalId)
            self.assertNotEqual(a,b)
            with Image.open(a) as image: pixels=np.asarray(image).astype(int)
            self.assertEqual(np.count_nonzero(pixels[:,:,2] > pixels[:,:,0]+30),0)
            with Image.open(b) as image: pixels=np.asarray(image).astype(int)
            self.assertEqual(np.count_nonzero(pixels[:,:,0] > pixels[:,:,2]+30),0)
            with self.assertRaisesRegex(thumbnails.ThumbnailError,'not part'):
                thumbnails.model_thumbnail(source,self.root/'cache',building_guid=ifcopenshell.guid.new())
        self.assertEqual(source.read_bytes(),before)

    def test_invalid_building_identifiers_and_missing_sources_fail_readably(self):
        for value in ['../source', 'https://example.test', 0]:
            with self.assertRaisesRegex(thumbnails.ThumbnailError,'identifier'):
                thumbnails.model_thumbnail(self.root/'missing.ifc',self.root/'cache',building_guid=value)
        with self.assertRaisesRegex(thumbnails.ThumbnailError,'IFC source'):
            thumbnails.model_thumbnail(self.root/'missing.ifc',self.root/'cache')

    def test_malformed_glb_fails_readably_without_creating_partial_png(self):
        self.glb.write_bytes(b'not a model')
        with self.assertRaises(thumbnails.ThumbnailError):self.render()
        self.assertFalse((self.root/'preview.png').exists())

    def test_per_pixel_depth_resolves_crossing_faces_that_centroid_sorting_cannot(self):
        projected=np.asarray([[[0,0],[10,0],[0,10]],[[0,0],[10,0],[0,10]]],dtype=float)
        depths=np.asarray([[8,0,0],[3,3,3]],dtype=float)
        colours=np.asarray([[220,30,30,255],[30,30,220,255]],dtype=np.uint8)
        pixels=np.asarray(thumbnails._rasterize(projected,depths,colours,np.asarray([True,True]),12,12))
        np.testing.assert_array_equal(pixels[1,1],[220,30,30])
        np.testing.assert_array_equal(pixels[1,6],[30,30,220])
        reversed_pixels=np.asarray(thumbnails._rasterize(projected[::-1],depths[::-1],colours[::-1],np.asarray([True,True]),12,12))
        np.testing.assert_array_equal(pixels,reversed_pixels)

    def test_glass_behind_opaque_walls_stays_hidden_and_front_glass_blends(self):
        triangle=np.asarray([[0,0],[10,0],[0,10]],dtype=float)
        projected=np.asarray([triangle,triangle,triangle])
        depths=np.asarray([[3,3,3],[2,2,2],[4,4,4]],dtype=float)
        colours=np.asarray([[200,100,50,255],[0,0,255,128],[50,200,100,128]],dtype=np.uint8)
        pixels=np.asarray(thumbnails._rasterize(projected,depths,colours,np.asarray([True,True,True]),12,12))
        np.testing.assert_allclose(pixels[1,1],[125,150,75],atol=1)

    def test_truncated_native_geometry_cache_is_rebuilt_once_without_changing_ifc(self):
        source=self.root/'real-cache.ifc';fixtures.IfcPassportTests().model().write(str(source))
        before=hashlib.sha256(source.read_bytes()).hexdigest()
        geometry_cache=self.root/'native-geometry';geometry_cache.mkdir()
        existing=thumbnails.model_glb(source,geometry_cache)
        complete=existing.read_bytes();existing.write_bytes(complete[:80])
        output=thumbnails.model_thumbnail(source,self.root/'preview-cache',geometry_cache_directory=geometry_cache)
        self.assertTrue(thumbnails._valid_png(output));self.assertGreater(existing.stat().st_size,80)
        self.assertFalse(thumbnails._corrupt_cached_glb(existing))
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before)

    def test_preview_policy_limits_do_not_trigger_native_cache_regeneration(self):
        source=self.root/'source.ifc';source.write_text('cache size policy fixture')
        with patch.object(thumbnails,'MAX_GLB_BYTES',40),patch.object(thumbnails,'model_glb',return_value=self.glb) as native:
            self.assertFalse(thumbnails._corrupt_cached_glb(self.glb))
            with self.assertRaisesRegex(thumbnails.ThumbnailError,'size limit'):
                thumbnails.model_thumbnail(source,self.root/'cache')
            self.assertEqual(native.call_count,1)
            self.assertTrue(self.glb.exists())

    def test_cross_worker_locks_limit_builders_and_same_source(self):
        cache=self.root/'cache';cache.mkdir()
        with thumbnails._generation_lock(cache,'one'), thumbnails._generation_lock(cache,'two'):
            with patch.object(thumbnails.time,'monotonic',side_effect=[0,20]):
                with self.assertRaisesRegex(thumbnails.ThumbnailError,'Other model previews'):
                    with thumbnails._generation_lock(cache,'three'):self.fail('A third builder must wait')
            with patch.object(thumbnails.time,'monotonic',side_effect=[0,20]):
                with self.assertRaisesRegex(thumbnails.ThumbnailError,'This model preview'):
                    with thumbnails._generation_lock(cache,'one'):self.fail('A duplicate source builder must wait')
        with thumbnails._generation_lock(cache,'one'):pass

    def test_completed_preview_reuse_does_not_wait_for_another_global_builder(self):
        cache=self.root/'cache';cache.mkdir()
        with thumbnails._generation_lock(cache,'one'),thumbnails._generation_lock(cache,'two'):
            with thumbnails._generation_lock(cache,'three',ready=lambda:True):pass

    def test_native_ifc_geometry_generates_a_nonempty_preview_and_keeps_original(self):
        source=self.root/'real-fixture.ifc';fixtures.IfcPassportTests().model().write(str(source))
        before=hashlib.sha256(source.read_bytes()).hexdigest()
        output=thumbnails.model_thumbnail(source,self.root/'native-cache')
        with Image.open(output) as image:
            pixels=np.asarray(image)
            self.assertGreater(np.count_nonzero(np.any(pixels != thumbnails.BACKGROUND,axis=2)),100)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before)


if __name__ == '__main__':unittest.main()
