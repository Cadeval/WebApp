# Generated by Django 5.1.1 on 2024-09-15 14:56

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('library', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='cadeviluser',
            name='is_staff',
            field=models.BooleanField(default=False, help_text='Designates whether the user can log into this admin site.', verbose_name='is staff'),
        ),
        migrations.AlterField(
            model_name='cadeviluser',
            name='is_superuser',
            field=models.BooleanField(default=False, help_text='Designates whether this user can view hidden content.', verbose_name='is superuser'),
        ),
    ]
