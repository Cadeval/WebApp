"""Presentation-only model choices; validation stays with ModelChoiceField."""
from pathlib import PurePosixPath

from django import forms
from django.urls import reverse


class ModelThumbnailChoices:
    template_name = 'shared/widgets/model_choices.html'
    option_template_name = 'shared/widgets/model_choice.html'

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        instance = getattr(value, 'instance', None)
        option['thumbnail_url'] = ''
        option['thumbnail_label'] = str(label)
        option['source_name'] = ''
        option['assessment_id'] = ''
        if instance is not None:
            upload = getattr(instance, 'upload', None) or instance
            source_name = PurePosixPath(upload.document.name).name
            title = instance.description or source_name or str(label)
            option['thumbnail_url'] = reverse('bim:model_thumbnail', args=[upload.pk])
            option['thumbnail_label'] = title
            option['source_name'] = source_name
            if upload is not instance:
                option['assessment_id'] = str(instance.pk)[:8]
        return option


class ModelThumbnailRadioSelect(ModelThumbnailChoices, forms.RadioSelect):
    pass


class ModelThumbnailCheckboxSelect(ModelThumbnailChoices, forms.CheckboxSelectMultiple):
    pass
