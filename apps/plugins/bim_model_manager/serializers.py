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
            'fassaden_oeffnungsflaeche',
            'fassaden_oeffnungsflaeche_unit',
            'fassaden_opake_flaeche',
            'fassaden_opake_flaeche_unit',
            'fenster_wand_verhaeltnis',
            'stockwerke',
            'energie_bewertung',
            'energy_status',
            'annual_site_energy_kwh',
            'annual_electricity_kwh',
            'annual_natural_gas_kwh',
            'energy_use_intensity_kwh_m2_year',
            'energy_error',
            'energy_assumptions',
            'energy_weather_file',
            'openstudio_version',
            'energy_simulated_at',
        ]


class CalculationConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = CalculationConfig
        fields = ['id', 'user', 'config', 'upload']
        read_only_fields = ['id', 'user']


class CadevilDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = CadevilDocument
        fields = ['id', 'user', 'group', 'upload', 'is_active', 'description']
        read_only_fields = ['id', 'user']


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
            'penrt_ml_a1_a3',
            'gwp_ml_a1_a3',
            'ap_ml_a1_a3',
            'penrt_ml_a1_a3_b4',
            'gwp_ml_a1_a3_b4',
            'ap_ml_a1_a3_b4',
            'penrt_ml_lz',
            'gwp_ml_lz',
            'ap_ml_lz',
            'recyclable_mass',
            'waste_mass',
        ]
