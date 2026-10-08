from django.db import migrations


def fix_assembled_asset_availability(apps, schema_editor):
    Asset = apps.get_model('inventory', 'Asset')
    Asset.objects.filter(status='assembled').update(availability_state='unavailable')


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0021_asset_status_dimensions'),
    ]

    operations = [
        migrations.RunPython(fix_assembled_asset_availability, migrations.RunPython.noop),
    ]
