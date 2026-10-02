from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [('shared', '0003_alter_cadeviluser_theme')]
    operations = [migrations.AlterModelOptions(name='cadeviluser', options={
        'verbose_name': 'user', 'verbose_name_plural': 'users',
        'permissions': [('view_application_logs', 'Can view live application logs')],
    })]
