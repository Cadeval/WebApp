# This will make sure the app is always imported when
# Django starts so that shared_task will use this app.
from model_manager.celery import app as cadevil_taskqueue


# __all__ = ('IfcExtractor',)
#
# from model_manager.ifc_extractor.data_models import IfcExtractor
