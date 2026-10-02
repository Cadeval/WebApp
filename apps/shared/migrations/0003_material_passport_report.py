from django.db import migrations, models
from django.core.validators import MinValueValidator

MATERIAL_FIELDS = ["global_brutto_price", "local_brutto_price", "local_netto_price",
    "volume", "area", "length", "mass", "waste_mass", "recyclable_mass"] + [
    f"{indicator}_ml_{period}" for indicator in ("gwp", "ap", "penrt")
    for period in ("a1_a3", "a1_a3_b4", "lz")]

class Migration(migrations.Migration):
    # Report storage does not depend on the unrelated pending theme change.
    dependencies = [("shared", "0001_initial")]
    run_before = [("shared", "0002_alter_cadeviluser_theme")]
    operations = [migrations.AddField(model_name="buildingmetrics", name="assessment_report",
        field=models.JSONField(default=dict, blank=True))] + [
        migrations.AlterField(model_name="materialproperties", name=name,
            field=models.FloatField(default=None, null=True, blank=True,
                validators=[] if name.startswith("gwp_") else [MinValueValidator(0.0)]))
        for name in MATERIAL_FIELDS]
