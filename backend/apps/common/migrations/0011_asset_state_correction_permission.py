from django.db import migrations


GROUP_NAME = 'warehouse_state_correction'


def create_group(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.get_or_create(name=GROUP_NAME)


def remove_group(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name=GROUP_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [('common', '0010_asset_identity_permissions')]
    operations = [migrations.RunPython(create_group, remove_group)]
