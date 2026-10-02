"""Independent schema validation in a spawned process; no native objects cross IPC."""
import asyncio
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
import logging
import threading

# At most one validation subprocess per application process.
_VALIDATION_SLOT=threading.BoundedSemaphore(1)


def validation_source(path):
    import ifcopenshell
    import ifcopenshell.validate
    from .diagnostics import schema_diagnostics
    from .material_assessment import file_hash
    before=file_hash(path)
    model=ifcopenshell.open(path)
    logger=ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(model,logger,express_rules=True)
    diagnostics=schema_diagnostics(model,logger.statements)
    if file_hash(path)!=before:raise ValueError('IFC source changed during validation; retry with a stable file.')
    return {'source_sha256':before,'diagnostics':diagnostics}


def should_parallelize(path, enabled=None):
    from django.conf import settings
    if enabled is None:
        enabled=getattr(settings,'IFC_PARALLEL_VALIDATION',True) if settings.configured else True
        minimum=getattr(settings,'IFC_PARALLEL_VALIDATION_MIN_BYTES',2_000_000) if settings.configured else 2_000_000
        enabled=enabled and Path(path).stat().st_size>=minimum
    return bool(enabled) and (os.cpu_count() or 1)>1


def apply_validation(report, result):
    from .diagnostics import grouped_diagnostics
    if result['source_sha256']!=report['provenance']['ifc_sha256']:
        raise ValueError('IFC source changed between calculation and validation; retry with a stable file.')
    diagnostics=result['diagnostics']
    report['schema_validation']={'checked':True,'valid':not diagnostics,'occurrences':len(diagnostics),'diagnostics':diagnostics}
    report['complete']=report['calculation_complete'] and not diagnostics
    from .element_identity import enrich_report
    report=enrich_report(report)
    report['diagnostic_groups']=grouped_diagnostics(report)
    return report


def parallel_assessment(calculate, path, config, options, geometry_workers):
    with _VALIDATION_SLOT:
        return _parallel_assessment(calculate,path,config,options,geometry_workers)


def _parallel_assessment(calculate, path, config, options, geometry_workers):
    report=None
    try:
        with ProcessPoolExecutor(max_workers=1,mp_context=multiprocessing.get_context('spawn')) as pool:
            validation=pool.submit(validation_source,str(Path(path).resolve()))
            report=calculate(path,config,options,validate_schema=False,geometry_workers=geometry_workers)
            result=validation.result()
    except (BrokenProcessPool,OSError,RuntimeError) as error:
        logging.getLogger(__name__).warning('Parallel validation unavailable; completing serial validation: %s',error)
        if report is None:return calculate(path,config,options,validate_schema=True,geometry_workers=geometry_workers)
        result=validation_source(str(Path(path).resolve()))
    return apply_validation(report,result)


async def assess_ifc_async(path,config,options=None,**kwargs):
    """Await a complete assessment without blocking the caller's event loop."""
    from .ifc_assessment import assess_ifc
    return await asyncio.to_thread(assess_ifc,path,config,options,**kwargs)
