import asyncio

from model_manager.backend import ProgressRecorder
from model_manager.ifc_extractor.data_models import IfcExtractor


@task_postrun.connect(retry=True)
def task_postrun_handler(**kwargs):
    """Runs after a task has finished. This will update the result backend to include the IGNORED result state.

    Necessary for HTTP to properly receive ignored task event."""
    if kwargs.pop("state") == "IGNORED":
        task = kwargs.pop("task")
        task.update_state(state="IGNORED", meta=str(kwargs.pop("retval")))


# Define Celery tasks that utilize the class methods
@shared_task(bind=True)
def process_ifc_file_task(self, ifc_file_path):
    progress_recorder = ProgressRecorder(self)
    extractor = IfcExtractor(ifc_file_path)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(
        extractor.process_products(progress_recorder=progress_recorder)
    )
    loop.close()
    # You can now save the results from extractor.properties, etc.
    # For example:
    # save_extraction_results(extractor.properties, extractor.material_dict)


@shared_task(bind=True)
def calculate_material_cost_task(self, ifc_file_path, product_id):
    extractor = IfcExtractor(ifc_file_path)
    product = extractor.ifc_model.by_id(product_id)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(extractor.calculate_material_cost(product))
    loop.close()
    # Handle results as needed


@shared_task(bind=True)
def calculate_norm1800_task(self, ifc_file_path, product_id):
    extractor = IfcExtractor(ifc_file_path)
    product = extractor.ifc_model.by_id(product_id)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(extractor.calculate_norm1800(product))
    loop.close()
    # Handle results as needed


@shared_task(bind=True)
def calculate_facade_area_task(self, ifc_file_path, product_id):
    extractor = IfcExtractor(ifc_file_path)
    product = extractor.ifc_model.by_id(product_id)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(extractor.calculate_facade_area(product))
    loop.close()
    # Handle results as needed
