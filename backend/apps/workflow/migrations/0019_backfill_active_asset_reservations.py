from django.db import migrations


def backfill_active_asset_reservations(apps, schema_editor):
    Asset = apps.get_model('inventory', 'Asset')
    InventoryReservation = apps.get_model('workflow', 'InventoryReservation')
    asset_ids = InventoryReservation.objects.filter(
        status='active',
        asset_id__isnull=False,
    ).values_list('asset_id', flat=True)
    Asset.objects.filter(pk__in=asset_ids).update(availability_state='reserved')


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0022_fix_assembled_asset_availability'),
        ('workflow', '0018_alter_inventorytransaction_action_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_active_asset_reservations, migrations.RunPython.noop),
    ]
