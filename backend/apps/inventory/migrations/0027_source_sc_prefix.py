from django.db import migrations, models


SC_PREFIXES = {
    ('JQR', 'G1EDU'): 'SC-G1-YS',
    ('JQR', 'GO2EDU'): 'SC-GO2-YS',
    ('JQR', 'G1D'): 'SC-G1D-YS',
    ('JQR', 'B2W'): 'SC-B2W-YS',
    ('JQR', 'B2'): 'SC-B2-YS',
    ('JQR', 'A2PRO'): 'SC-A2-YS',
    ('JQR', 'A2W'): 'SC-A2W-YS',
    ('JQR', 'AS2U1'): 'SC-AS2-YS',
    ('JQR', 'AS2W'): 'SC-AS2W-YS',
    ('JQR', 'H2'): 'SC-H2-YS',
    ('JQR', 'X2'): 'SC-X2-ZY',
    ('CD', 'A2'): 'SC-A2-CDB-YS',
    ('CD', 'G1'): 'SC-G1-DC-YS',
    ('LD', 'HELIOS32'): 'SC-LD-ST',
    ('LD', 'LIVOXMID360S'): 'SC-LD-MID360S',
    ('YT', 'HMTD5737T7W'): 'SC-YT-HK7W',
    ('YT', 'HMTD5567T15WGLT'): 'SC-YT-HK15W',
    ('YT', 'HYD330F'): 'SC-YT-HY',
    ('YT', 'C14PRO'): 'SC-YT-YZ',
    ('ZJ', 'I913900HES32G512G'): 'SC-MNZJ-HT',
    ('XJ', 'ORBBECGEMINI335'): 'SC-SDXJ-OBZG',
    ('SZ', 'RH56DFX2R'): 'SC-LQS-YS',
    ('QT', 'A20'): 'SC-A20-LY',
    ('XJ', 'SY4MUIR'): 'SC-SMHWQJ-SY',
    ('XJ', 'SYOMNID'): 'SC-SMQJ-SY',
}


def seed_source_prefixes(apps, schema_editor):
    ProductModelCode = apps.get_model('inventory', 'ProductModelCode')
    for (type_code, model_code), prefix in SC_PREFIXES.items():
        ProductModelCode.objects.filter(
            asset_type_code__code=type_code,
            code=model_code,
        ).update(sc_prefix=prefix, sc_serial_width=4)

    AssetTypeCode = apps.get_model('inventory', 'AssetTypeCode')
    Manufacturer = apps.get_model('inventory', 'Manufacturer')
    hand_type = AssetTypeCode.objects.filter(code='SZ').first()
    qn = Manufacturer.objects.filter(code='QN').first()
    if hand_type:
        for code, name, prefix in (
            ('QNY', '强脑灵巧手 Y 型', 'SC-LQS-QN-Y'),
            ('QNZ', '强脑灵巧手 Z 型', 'SC-LQS-QN-Z'),
            ('QNCJY', '强脑灵巧手采集版 Y 型', 'SC-LQS-QNCJ-Y'),
            ('QNCJZ', '强脑灵巧手采集版 Z 型', 'SC-LQS-QNCJ-Z'),
        ):
            ProductModelCode.objects.get_or_create(
                asset_type_code=hand_type,
                code=code,
                defaults={
                    'manufacturer': qn,
                    'name': name,
                    'sc_prefix': prefix,
                    'sc_serial_width': 4,
                },
            )

    charge_type = AssetTypeCode.objects.filter(code='CD').first()
    unit = Manufacturer.objects.filter(code='UNIT').first()
    if charge_type:
        ProductModelCode.objects.get_or_create(
            asset_type_code=charge_type,
            code='B2BAT',
            defaults={
                'manufacturer': unit,
                'name': 'B2电池',
                'sc_prefix': 'SC-B2-DC-YS',
                'sc_serial_width': 4,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0026_asset_state_correction_and_label_leases'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='assettypecode',
            options={
                'ordering': ['code'],
                'verbose_name': '设备类型代码',
                'verbose_name_plural': '设备类型代码',
            },
        ),
        migrations.AddField(
            model_name='productmodelcode',
            name='sc_prefix',
            field=models.CharField(
                blank=True,
                default='',
                help_text='填写源表实际使用的完整前缀，例如 SC-G1-YS；系统只在末尾续流水号。',
                max_length=80,
                verbose_name='SC 编码前缀',
            ),
        ),
        migrations.AddField(
            model_name='productmodelcode',
            name='sc_serial_width',
            field=models.PositiveSmallIntegerField(
                default=4,
                help_text='源表通常使用 4 位流水，例如 0001。',
                verbose_name='SC 流水位数',
            ),
        ),
        migrations.AlterField(
            model_name='asset',
            name='code_source',
            field=models.CharField(
                choices=[
                    ('warehouse_assigned', '源表既有资产编码'),
                    ('market_generated', '系统生成 SC 编码'),
                    ('admin_assigned', '行政/ERP DZSB 编码'),
                    ('pending_admin', '已确认资产，暂未赋码'),
                ],
                default='pending_admin',
                max_length=24,
                verbose_name='主资产编码来源',
            ),
        ),
        migrations.AlterField(
            model_name='assetcodesequence',
            name='scheme',
            field=models.CharField(
                choices=[
                    ('system', '系统永久编号'),
                    ('market', '仓库自编 SC 资产编码'),
                ],
                max_length=16,
                verbose_name='编码体系',
            ),
        ),
        migrations.RunPython(seed_source_prefixes, migrations.RunPython.noop),
    ]
