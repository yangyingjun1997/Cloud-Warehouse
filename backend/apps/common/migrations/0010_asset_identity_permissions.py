from django.db import migrations


GROUPS = (
    'warehouse_code_master',
    'warehouse_admin_code',
    'warehouse_domain_change',
    'warehouse_import',
    'warehouse_label_print',
)


def create_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    for name in GROUPS:
        Group.objects.get_or_create(name=name)


def remove_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name__in=GROUPS).delete()


class Migration(migrations.Migration):
    dependencies = [('common', '0009_securityauditevent')]
    operations = [migrations.RunPython(create_groups, remove_groups)]
