from asgiref.sync import sync_to_async
from django.forms import Form, ModelForm, ChoiceField
from model_manager.models import CadevilDocument, FileUpload, ConfigUpload, CalculationConfig
from django import forms
from django.contrib.auth.models import Group


class ConfigUploadForm(ModelForm):
    class Meta:
        model = ConfigUpload
        fields = (
            "description",
            "document",
        )
        db_table = "archicad_eval_config_uploads"

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        self.user_id = kwargs.pop("user_id", None)
        super().__init__(*args, **kwargs)

    async def asave(self, commit=True):
        instance = super().save(commit=False)
        instance.user = self.user
        instance.user_id = self.user_id
        if commit:
            await sync_to_async(instance.save)()
        return instance


class CalculationConfigForm(forms.ModelForm):
    class Meta:
        model = CalculationConfig
        # Choose which fields to expose in your form
        fields = ['upload']


class GroupChangeForm(Form):
    group_field = ChoiceField()

    def __init__(self, *args, **kwargs):
        self.user_groups = kwargs.pop("user_groups", [])
        super().__init__(*args, **kwargs)
        self.fields["group_field"].choices = [
            (i, group) for i, group in enumerate(self.user_groups)
        ]
        self.fields["group_field"].initial = (
            self.user_groups[0] if self.user_groups else None
        )


class GroupForm(forms.ModelForm):
    class Meta:
        model = Group
        # You can specify the fields you want to expose in the form
        fields = ['name', 'permissions']

class DocumentForm(ModelForm):
    class Meta:
        model = CadevilDocument
        fields = ("description",)
        db_table = "archicad_eval_document"

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        self.user_id = kwargs.pop("user_id", None)
        super().__init__(*args, **kwargs)

    async def asave(self, commit=True):
        instance = super().save(commit=False)
        instance.user = self.user
        if commit:
            await sync_to_async(instance.save)()
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

    async def asave(self, commit=True):
        instance = super().save(commit=False)
        instance.user = self.user
        instance.user_id = self.user_id
        if commit:
            await sync_to_async(instance.save)()
        return instance
