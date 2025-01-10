from rest_framework import serializers
from model_manager.models import BuildingMetrics, CadevilDocument, CalculationConfig, ConfigUpload, FileUpload, \
    MaterialProperties


class BuildingMetricserializer(serializers.ModelSerializer):
    class Meta:
        model = BuildingMetrics
        fields = [
            'id',
            'project',
            'grundstuecksfläche',
            'grundstuecksfläche_unit',
            'bebaute_fläche',
            'bebaute_fläche_unit',
            'unbebaute_fläche',
            'unbebaute_fläche_unit',
            'brutto_rauminhalt',
            'brutto_rauminhalt_unit',
            'brutto_grundfläche',
            'brutto_grundfläche_unit',
            'konstruktions_grundfläche',
            'konstruktions_grundfläche_unit',
            'netto_raumfläche',
            'netto_raumfläche_unit',
            'bgf_bf_ratio',
            'bri_bgf_ratio',
            'fassadenflaeche',
            'fassadenflaeche_unit',
            'stockwerke',
            'energie_bewertung',
        ]


class CalculationConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = CalculationConfig
        fields = ['id', 'user', 'config', 'upload']


class CadevilDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = CadevilDocument
        fields = ['id', 'user', 'group', 'upload', 'is_active', 'description']


class ConfigUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConfigUpload
        fields = ['id', 'user', 'description', 'document', 'uploaded_at']
        read_only_fields = ['id', 'user', 'uploaded_at']


class FileUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = FileUpload
        fields = ['id', 'user', 'description', 'document', 'uploaded_at']
        read_only_fields = ['id', 'user', 'description', 'document', 'uploaded_at']


class MaterialPropertiesSerializer(serializers.ModelSerializer):
    class Meta:
        model = MaterialProperties
        fields = [
            'id',
            'project',
            'name',
            'global_brutto_price',
            'local_brutto_price',
            'local_netto_price',
            'volume',
            'area',
            'length',
            'mass',
            'penrt_ml',
            'gwp_ml',
            'ap_ml',
            'recyclable_mass',
            'waste_mass',
        ]
