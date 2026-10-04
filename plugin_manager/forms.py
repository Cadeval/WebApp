import hashlib
from pathlib import Path

from django import forms
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile

from .models import PluginRecord
from .packages import validate_package
from .signatures import verify_package_signature
from .archives import normalize_archive


class PluginUploadForm(forms.Form):
    artifact = forms.FileField(
        label="Signed plugin package",
        widget=forms.ClearableFileInput(attrs={"accept": ".zip,.tar,.tar.gz,.tgz,.tar.xz,.txz"}),
    )

    def clean_artifact(self):
        artifact = self.cleaned_data["artifact"]
        max_size = getattr(settings, "PLUGIN_MAX_UPLOAD_SIZE", 2 * 1024 * 1024)
        if not artifact.size:
            raise forms.ValidationError("Plugin packages must not be empty.")
        if artifact.size > max_size:
            raise forms.ValidationError(f"Plugin packages may not exceed {max_size} bytes.")
        if Path(artifact.name).suffix.lower() not in {".zip", ".tar", ".tgz", ".gz", ".txz", ".xz"}:
            raise forms.ValidationError(
                "Upload a signed ZIP, TAR, tar.gz or tar.xz package containing plugin.json. Single JavaScript and WASM files are not accepted. OpenZL decoding is not installed."
            )
        content = artifact.read()
        normalized, archive_format = normalize_archive(content)
        manifest = validate_package(normalized)
        key = verify_package_signature(manifest)
        if PluginRecord.objects.filter(plugin_id=manifest["id"]).exists():
            raise forms.ValidationError("A plugin with this package id already exists.")
        package = SimpleUploadedFile("package.zip", normalized, content_type="application/zip")
        package.package_manifest = {
            **manifest,
            "archive_format": archive_format,
            "uploaded_archive_sha256": hashlib.sha256(content).hexdigest(),
        }
        package.signing_key = key
        package.content_hash = hashlib.sha256(normalized).hexdigest()
        return package
