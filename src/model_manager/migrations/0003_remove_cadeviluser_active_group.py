# Generated by Django 5.1.2 on 2024-10-19 20:04

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("model_manager", "0002_alter_cadeviluser_is_staff_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="cadeviluser",
            name="active_group",
        ),
    ]
