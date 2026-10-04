"""Remove retained CityJSON only after the owning database transaction succeeds."""
import logging
from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver
from .models import ModelConversion


@receiver(post_delete, sender=ModelConversion)
def remove_cityjson_source(sender, instance, **kwargs):
    name, storage = instance.source.name, instance.source.storage
    def cleanup():
        try:
            if name:
                storage.delete(name)
        except OSError:
            logging.getLogger(__name__).exception("Could not remove retained CityJSON source")
    transaction.on_commit(cleanup)
