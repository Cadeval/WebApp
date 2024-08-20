from django.forms import ModelForm
from .models import CadevilDocument, FileUpload


class DocumentForm(ModelForm):
    class Meta:
        model = CadevilDocument
        fields = ("description",)
        db_table = "archicad_eval_document"

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        self.user_id = kwargs.pop("user_id", None)
        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.user = self.user
        if commit:
            instance.user = self.user
            instance.save()
        return instance


class UploadForm(ModelForm):
    class Meta:
        model = FileUpload
        fields = (
            "description",
            "document",
        )
        db_table = "archicad_eval_uploads"

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        self.user_id = kwargs.pop("user_id", None)
        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.user = self.user
        if commit:
            instance.user = self.user
            instance.user_id = self.user_id
            instance.save()
        return instance
