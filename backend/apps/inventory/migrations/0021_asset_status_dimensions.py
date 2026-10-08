from django.db import migrations, models


LEGACY_STATUS_DIMENSIONS = {
    'in_stock': ('in_warehouse', 'available', 'normal', 'internal'),
    'borrowed': ('out_on_loan', 'unavailable', 'normal', 'internal'),
    'issued': ('out_issued', 'unavailable', 'normal', 'internal'),
    'pending_out': ('pending_out', 'reserved', 'normal', 'internal'),
    'pending_return': ('pending_return', 'unavailable', 'pending_inspection', 'internal'),
    'pending_inspection': ('in_warehouse', 'unavailable', 'pending_inspection', 'internal'),
    'maintenance': ('in_warehouse', 'unavailable', 'maintenance', 'internal'),
    'repairing': ('external_repair', 'unavailable', 'repairing', 'internal'),
    'sold': ('delivered', 'unavailable', 'normal', 'sold'),
    'internal_borrow': ('in_warehouse', 'unavailable', 'normal', 'internal'),
    'shipped_out': ('in_transit', 'unavailable', 'normal', 'internal'),
    'assembled': ('in_warehouse', 'available', 'normal', 'internal'),
    'returned_to_vendor': ('vendor', 'unavailable', 'normal', 'returned_to_vendor'),
    'scrapped': ('in_warehouse', 'unavailable', 'normal', 'scrapped'),
    'damaged': ('in_warehouse', 'unavailable', 'damaged', 'internal'),
    'lost': ('in_transit', 'unavailable', 'lost', 'internal'),
    'disabled': ('in_warehouse', 'frozen', 'normal', 'disabled'),
    'converted_to_stock': ('in_warehouse', 'unavailable', 'normal', 'converted_to_stock'),
}


def backfill_asset_status_dimensions(apps, schema_editor):
    Asset = apps.get_model('inventory', 'Asset')
    for legacy_status, dimensions in LEGACY_STATUS_DIMENSIONS.items():
        location_state, availability_state, quality_state, disposition_state = dimensions
        Asset.objects.filter(status=legacy_status).update(
            location_state=location_state,
            availability_state=availability_state,
            quality_state=quality_state,
            disposition_state=disposition_state,
        )


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0020_asset_system_asset_no'),
    ]

    operations = [
        migrations.AddField(
            model_name='asset',
            name='location_state',
            field=models.CharField(
                choices=[
                    ('in_warehouse', '在仓'), ('out_on_loan', '外借中'), ('out_issued', '已领用'),
                    ('pending_out', '待出库'), ('pending_return', '待归还验收'),
                    ('external_repair', '外部维修中'), ('in_transit', '运输中'),
                    ('delivered', '已交付'), ('vendor', '供应商处'),
                ],
                default='in_warehouse', max_length=32, verbose_name='实物位置状态',
            ),
        ),
        migrations.AddField(
            model_name='asset',
            name='availability_state',
            field=models.CharField(
                choices=[
                    ('available', '可申请'), ('reserved', '已预占'),
                    ('unavailable', '暂不可用'), ('frozen', '已冻结'),
                ],
                default='available', max_length=24, verbose_name='可用性状态',
            ),
        ),
        migrations.AddField(
            model_name='asset',
            name='quality_state',
            field=models.CharField(
                choices=[
                    ('normal', '正常'), ('pending_inspection', '待质检'),
                    ('maintenance', '待维修'), ('repairing', '维修中'),
                    ('damaged', '损坏'), ('lost', '丢失'),
                ],
                default='normal', max_length=24, verbose_name='质量状态',
            ),
        ),
        migrations.AddField(
            model_name='asset',
            name='disposition_state',
            field=models.CharField(
                choices=[
                    ('internal', '内部使用'), ('sold', '已售出'),
                    ('returned_to_vendor', '已退供应商'), ('scrapped', '已报废'),
                    ('disabled', '已停用'), ('converted_to_stock', '已转为耗材配件'),
                ],
                default='internal', max_length=32, verbose_name='处置状态',
            ),
        ),
        migrations.RunPython(backfill_asset_status_dimensions, migrations.RunPython.noop),
    ]
