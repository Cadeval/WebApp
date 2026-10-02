import logging


class InMemoryLogHandler:
    """Compatible calculation progress sink during the API migration.

    The persistent assessment report, not this diagnostic log, carries omissions.
    """
    def sync_emit(self,record,user_id=None):
        logging.getLogger('material_assessment').info('%s',record,extra={'user_id':user_id})
