"""Forms used by the active native BIM pages."""
from django import forms

from apps.shared.models import CalculationConfig, ConfigUpload, FileUpload


class ConfigUploadForm(forms.ModelForm):
    class Meta:
        model = ConfigUpload
        fields = ("description", "document")

    def __init__(self, *args, **kwargs):
        kwargs.pop("user", None)
        super().__init__(*args, **kwargs)


class CalculationConfigForm(forms.ModelForm):
    class Meta:
        model = CalculationConfig
        fields = ("upload",)

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields["upload"].queryset = ConfigUpload.objects.filter(user=user)


class UploadForm(forms.ModelForm):
    class Meta:
        model = FileUpload
        fields = ("description", "document")

    def __init__(self, *args, **kwargs):
        kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
