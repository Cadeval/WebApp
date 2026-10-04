import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures.process import BrokenProcessPool
from django.test import override_settings
from .ifc_assessment import assess_ifc
from .assessment_futures import assess_ifc_async, should_parallelize
from .test_material_assessment import IfcPassportTests, reference
from .material_assessment import file_hash

class AssessmentFutureTests(unittest.TestCase):
    def test_spawned_future_matches_serial_valid_and_invalid_reports(self):
        for invalid in [False,True]:
            with tempfile.TemporaryDirectory() as folder:
                model=IfcPassportTests().model()
                if invalid:model.by_type('IfcWall')[0].ObjectPlacement=None
                path=Path(folder)/'source.ifc';model.write(str(path));before=file_hash(path)
                serial=assess_ifc(path,reference(),parallel_validation=False)
                parallel=assess_ifc(path,reference(),parallel_validation=True)
                self.assertEqual(serial,parallel)
                self.assertEqual(file_hash(path),before)

    def test_broken_process_falls_back_without_losing_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            model=IfcPassportTests().model();model.by_type('IfcWall')[0].ObjectPlacement=None
            path=Path(folder)/'source.ifc';model.write(str(path))
            serial=assess_ifc(path,reference(),parallel_validation=False)
            with patch('apps.plugins.bim_model_manager.ifc_extractor.assessment_futures.ProcessPoolExecutor',side_effect=BrokenProcessPool('failed')):
                parallel=assess_ifc(path,reference(),parallel_validation=True)
            self.assertEqual(serial,parallel)
            self.assertFalse(parallel['schema_validation']['valid'])

    def test_small_files_avoid_default_process_startup(self):
        with tempfile.NamedTemporaryFile() as source:
            source.write(b'IFC');source.flush()
            self.assertFalse(should_parallelize(source.name))

    def test_large_files_use_configurable_parallelism(self):
        with tempfile.NamedTemporaryFile() as source:
            source.truncate(2_000_000)
            with patch('apps.plugins.bim_model_manager.ifc_extractor.assessment_futures.os.cpu_count', return_value=4):
                with override_settings(IFC_PARALLEL_VALIDATION=True, IFC_PARALLEL_VALIDATION_MIN_BYTES=2_000_000):
                    self.assertTrue(should_parallelize(source.name))
                with override_settings(IFC_PARALLEL_VALIDATION=False):
                    self.assertFalse(should_parallelize(source.name))
            with patch('apps.plugins.bim_model_manager.ifc_extractor.assessment_futures.os.cpu_count', return_value=1):
                self.assertFalse(should_parallelize(source.name, True))

    def test_async_api_does_not_block_event_loop(self):
        async def exercise():
            progress=[]
            def slow(*args,**kwargs):
                time.sleep(.05)
                return {'complete':True}
            with patch('apps.plugins.bim_model_manager.ifc_extractor.ifc_assessment.assess_ifc',side_effect=slow):
                pending=asyncio.create_task(assess_ifc_async('source',{}))
                await asyncio.sleep(.005)
                progress.append(not pending.done())
                self.assertEqual(await pending,{'complete':True})
            self.assertEqual(progress,[True])
        asyncio.run(exercise())
