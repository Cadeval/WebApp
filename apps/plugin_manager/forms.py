import hashlib
import re
from pathlib import Path

from django import forms
from django.conf import settings

from apps.plugin_manager.models import PluginRecord

PLUGIN_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
JAVASCRIPT_CONTENT_TYPES = {"application/javascript", "text/javascript"}
WASM_CONTENT_TYPES = {"application/wasm"}


class PluginUploadForm(forms.Form):
    plugin_id = forms.CharField(max_length=255)
    name = forms.CharField(max_length=255)
    artifact = forms.FileField()

    def clean_plugin_id(self) -> str:
        plugin_id = self.cleaned_data["plugin_id"].strip()
        if not PLUGIN_ID_PATTERN.fullmatch(plugin_id):
            raise forms.ValidationError(
                "Use lowercase letters and numbers separated by dots, dashes, or underscores."
            )
        if PluginRecord.objects.filter(plugin_id=plugin_id).exists():
            raise forms.ValidationError("A plugin with this id already exists.")
        return plugin_id

    def clean_artifact(self):
        artifact = self.cleaned_data["artifact"]
        max_size = getattr(settings, "PLUGIN_MAX_UPLOAD_SIZE", 2 * 1024 * 1024)
        if not artifact.size:
            raise forms.ValidationError("Plugin files must not be empty.")
        if artifact.size > max_size:
            raise forms.ValidationError(
                f"Plugin files may not exceed {max_size} bytes."
            )

        suffix = Path(artifact.name).suffix.lower()
        content = artifact.read()
        artifact.seek(0)
        if suffix == ".wasm":
            if (
                artifact.content_type not in WASM_CONTENT_TYPES
                or not content.startswith(b"\x00asm\x01\x00\x00\x00")
            ):
                raise forms.ValidationError(
                    "The uploaded file is not valid WebAssembly."
                )
            artifact.plugin_type = PluginRecord.ArtifactType.WEBASSEMBLY
        elif suffix in {".js", ".mjs"}:
            if artifact.content_type not in JAVASCRIPT_CONTENT_TYPES:
                raise forms.ValidationError("The uploaded file is not JavaScript.")
            try:
                source = content.decode("utf-8")
            except UnicodeDecodeError as error:
                raise forms.ValidationError(
                    "JavaScript plugins must be UTF-8 text."
                ) from error
            if "\x00" in source or source.lstrip().lower().startswith(
                ("<!doctype", "<html", "<script")
            ):
                raise forms.ValidationError(
                    "The uploaded file is not a JavaScript worker module."
                )
            artifact.plugin_type = PluginRecord.ArtifactType.JAVASCRIPT
        else:
            raise forms.ValidationError(
                "Only .js, .mjs, and .wasm plugin files are accepted."
            )

        artifact.content_hash = hashlib.sha256(content).hexdigest()
        return artifact
