from datetime import date

from django.db import migrations, models


SEQUENCE_DATE = date(1900, 1, 1)


def populate_system_asset_numbers(apps, schema_editor):
    Asset = apps.get_model('inventory', 'Asset')
    DailySequence = apps.get_model('common', 'DailySequence')
    assets = list(Asset.objects.filter(system_asset_no__isnull=True).order_by('created_at', 'id'))
    if not assets:
        return
    sequence, _ = DailySequence.objects.get_or_create(
        prefix='AST', sequence_date=SEQUENCE_DATE, defaults={'last_number': 0},
    )
    number = sequence.last_number
    for asset in assets:
        number += 1
        asset.system_asset_no = f'AST-{number:06d}'
        # Legacy save() derived the QR value from the ERP code. Replace only
        # that derived value; preserve any explicitly imported QR content.
        if not asset.qr_value or asset.qr_value == asset.asset_code:
            asset.qr_value = asset.system_asset_no
        asset.save(update_fields=['system_asset_no', 'qr_value'])
    sequence.last_number = number
    sequence.save(update_fields=['last_number', 'updated_at'])


class Migration(migrations.Migration):
    dependencies = [
        ('common', '0004_dailysequence'),
        ('inventory', '0019_alter_compositeunit_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='asset',
            name='system_asset_no',
            field=models.CharField(blank=True, editable=False, max_length=32, null=True, unique=True, verbose_name='仓库系统资产编号'),
        ),
        migrations.RunPython(populate_system_asset_numbers, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='asset',
            name='system_asset_no',
            field=models.CharField(editable=False, max_length=32, unique=True, verbose_name='仓库系统资产编号'),
        ),
    ]
