import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from plugins.bim_model_manager.ifc_extractor.ifc_assessment import assess_ifc
from plugins.bim_model_manager.ifc_extractor.parallel_geometry import geometry_measurements, geometry_threads
from plugins.bim_model_manager.ifc_extractor.material_assessment import AssessmentOptions, file_hash
from tests.plugins.bim_model_manager.ifc_extractor.test_material_assessment import IfcPassportTests, reference

class ParallelGeometryTests(unittest.TestCase):
    def test_serial_parallel_reports_match_and_sources_remain_unchanged(self):
        import ifcopenshell.api.root
        import ifcopenshell.api.geometry
        import numpy as np
        for millimetres in (False, True):
            model=IfcPassportTests().model(millimetres)
            wall=model.by_type('IfcWall')[0]
            for i in range(12):
                other=ifcopenshell.api.root.copy_class(model, product=wall)
                other.Representation=wall.Representation
                matrix=np.eye(4);matrix[0,3]=(i+1)*7
                ifcopenshell.api.geometry.edit_object_placement(model, product=other, matrix=matrix)
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'model.ifc';model.write(str(path));before=file_hash(path)
                one=assess_ifc(path, reference(), AssessmentOptions(grade_weighting='mass'),geometry_workers=1)
                many=assess_ifc(path, reference(), AssessmentOptions(grade_weighting='mass'),geometry_workers=4)
                self.assertEqual(file_hash(path),before)
                self.assertEqual(many['geometry_processing']['converted'],13)
                one.pop('geometry_processing');many.pop('geometry_processing')
                self.assertEqual(one,many)
                self.assertAlmostEqual(many['building']['mass'],13*8250)

    def test_iterator_failure_falls_back_to_serial_measurement(self):
        model=IfcPassportTests().model();wall=model.by_type('IfcWall')[0]
        failed=MagicMock();failed.initialize.side_effect=RuntimeError('Iterator unavailable')
        with patch('ifcopenshell.geom.iterator',return_value=failed):
            values,stats=geometry_measurements(model,[wall],threads=4)
        self.assertAlmostEqual(values[wall.id()]['volume'],4)
        self.assertEqual(stats['serial_fallbacks'],1)

    def test_invalid_schema_is_nonfatal_and_geometry_still_runs(self):
        model=IfcPassportTests().model();model.by_type('IfcWall')[0].ObjectPlacement=None
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'invalid.ifc';model.write(str(path))
            report=assess_ifc(path,reference())
            self.assertFalse(report['schema_validation']['valid'])
            self.assertGreater(report['schema_validation']['occurrences'],0)
            self.assertGreater(report['geometry_processing']['converted'],0)
            self.assertFalse(report['complete'])

    def test_worker_limits_and_empty_input(self):
        with patch('os.cpu_count',return_value=8):
            self.assertEqual(geometry_threads(4),4);self.assertEqual(geometry_threads(100),8)
        with self.assertRaises(ValueError):geometry_threads(0)
        values,stats=geometry_measurements(IfcPassportTests().model(),[],threads=1)
        self.assertEqual(values,{});self.assertEqual(stats['converted'],0)
