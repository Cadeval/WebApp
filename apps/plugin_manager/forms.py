import hashlib
import re
from pathlib import Path

from django import forms
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.plugin_manager.models import PluginRecord
from .packages import validate_package
from .signatures import verify_package_signature
from .archives import normalize_archive

PLUGIN_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
JAVASCRIPT_CONTENT_TYPES = {"application/javascript", "text/javascript"}
WASM_CONTENT_TYPES = {"application/wasm"}


class PluginUploadForm(forms.Form):
    plugin_id = forms.CharField(max_length=255, required=False)
    name = forms.CharField(max_length=255, required=False)
    artifact = forms.FileField()

    def clean_plugin_id(self) -> str:
        plugin_id = self.cleaned_data["plugin_id"].strip()
        if plugin_id and not PLUGIN_ID_PATTERN.fullmatch(plugin_id):
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
        if suffix in {".zip",".tar",".tgz",".gz",".txz",".xz"}:
            original_hash=hashlib.sha256(content).hexdigest()
            normalized,archive_format=normalize_archive(content)
            manifest=validate_package(normalized)
            key=verify_package_signature(manifest)
            # Store one canonical private ZIP representation, preserving original provenance.
            artifact=SimpleUploadedFile("package.zip",normalized,content_type="application/zip")
            artifact.package_manifest={**manifest,"archive_format":archive_format,"uploaded_archive_sha256":original_hash}
            artifact.signing_key=key
            artifact.plugin_type=PluginRecord.ArtifactType.ZIP
            content=normalized
        elif suffix == ".wasm":
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
                "Use ZIP, TAR, tar.gz, tar.xz, .js, .mjs or .wasm. OpenZL decoding is not installed; repack its file contents into a supported container."
            )

        artifact.content_hash = hashlib.sha256(content).hexdigest()
        return artifact

    def clean(self):
        cleaned=super().clean()
        artifact=cleaned.get("artifact")
        manifest=getattr(artifact,"package_manifest",None)
        if manifest:
            for field,source in [("plugin_id","id"),("name","name")]:
                if cleaned.get(field) and cleaned[field]!=manifest[source]:
                    self.add_error(field,"For an archive upload, this value must match plugin.json. Leave it blank to use the manifest.")
                else: cleaned[field]=manifest[source]
            if PluginRecord.objects.filter(plugin_id=manifest["id"]).exists():
                self.add_error("artifact","A plugin with this package id already exists.")
        elif artifact:
            for field in ["plugin_id","name"]:
                if not cleaned.get(field): self.add_error(field,"This field is required for a single-file upload.")
        return cleaned
