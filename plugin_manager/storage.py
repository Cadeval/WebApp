"""Executable uploads must never be reachable through Bolt's public media mount."""
from pathlib import Path
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivatePluginStorage(FileSystemStorage):
    @property
    def base_location(self):
        media=Path(settings.MEDIA_ROOT).resolve()
        location=Path(getattr(settings,"PLUGIN_ARTIFACT_ROOT",media.parent / "plugin-artifacts")).resolve()
        if location == media or media in location.parents:
            raise ValueError("PLUGIN_ARTIFACT_ROOT must be outside MEDIA_ROOT.")
        return str(location)

    @property
    def location(self):
        return self.base_location
