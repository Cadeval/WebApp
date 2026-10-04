"""Forms for CityJSON exchange and owner-set building positions."""
import math
import re

from django import forms
from django.core.validators import FileExtensionValidator
from apps.plugins.bim_model_manager.django.models import BuildingLocation
from apps.plugins.bim_model_manager.django.uploads import validate_model_upload_size


class CityJSONImportForm(forms.Form):
    description = forms.CharField(max_length=255, required=False, label="Model name")
    document = forms.FileField(label="CityJSON file", validators=[
        FileExtensionValidator(["json", "cityjson"]), validate_model_upload_size],
        help_text="A CityJSON file (.city.json, .json or .cityjson). GeoJSON and JSON Lines are different formats.")
    lod = forms.CharField(max_length=8, required=False, label="Level of detail (optional)",
        help_text="Leave blank to use the highest available level, or enter an exact level such as 1.2.")
    name_attribute = forms.CharField(max_length=100, required=False,
        help_text="Optional CityJSON attribute containing the building name.")

    def clean_lod(self):
        value = self.cleaned_data["lod"].strip()
        if value and not re.fullmatch(r"[0-4](?:\.[0-9]{1,3})?", value):
            raise forms.ValidationError("Enter a level from 0 to 4, such as 1.2.")
        return value


class BuildingLocationForm(forms.ModelForm):
    class Meta:
        model = BuildingLocation
        fields = ["latitude", "longitude", "note"]
        labels = {"latitude": "Latitude (decimal degrees)", "longitude": "Longitude (decimal degrees)", "note": "Location note (optional)"}
        help_texts = {"latitude": "WGS84: −90 to 90. North is positive.", "longitude": "WGS84: −180 to 180. East is positive."}
        widgets = {"latitude": forms.NumberInput(attrs={"step": "any"}), "longitude": forms.NumberInput(attrs={"step": "any"})}

    def clean(self):
        data = super().clean()
        for field in ("latitude", "longitude"):
            value = data.get(field)
            if value is not None and not math.isfinite(value):
                self.add_error(field, "Enter a finite decimal coordinate.")
        return data
