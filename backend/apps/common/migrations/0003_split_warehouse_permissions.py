from django.db import migrations


PERMISSION_GROUPS = ('warehouse_entry', 'warehouse_outbound', 'warehouse_approval', 'warehouse_reports')


def split_legacy_warehouse_staff(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    legacy = Group.objects.filter(name='warehouse_staff').first()
    groups = [Group.objects.get_or_create(name=name)[0] for name in PERMISSION_GROUPS]
    if legacy:
        for user in legacy.user_set.all():
            user.groups.add(*groups)
            user.groups.remove(legacy)


class Migration(migrations.Migration):
    dependencies = [('common', '0002_operationauditlog')]
    operations = [migrations.RunPython(split_legacy_warehouse_staff, migrations.RunPython.noop)]
