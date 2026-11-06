import re
import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def seed_asset_identity(apps, schema_editor):
    Asset = apps.get_model('inventory', 'Asset')
    AssetCodeSequence = apps.get_model('inventory', 'AssetCodeSequence')
    AssetIdentifier = apps.get_model('inventory', 'AssetIdentifier')
    InventoryDomain = apps.get_model('inventory', 'InventoryDomain')

    market, _ = InventoryDomain.objects.get_or_create(
        code='MARKET',
        defaults={'name': '市场资产', 'code_scheme': 'market_generated', 'is_active': True},
    )
    rnd, _ = InventoryDomain.objects.get_or_create(
        code='RND',
        defaults={'name': '研发资产', 'code_scheme': 'admin_assigned', 'is_active': True},
    )

    highest_system_number = 0
    for asset in Asset.objects.select_related('warehouse').iterator():
        warehouse_text = f'{asset.warehouse.name} {asset.warehouse.code}' if asset.warehouse_id else ''
        domain = market if '市场' in warehouse_text else rnd
        old_qr = (asset.qr_value or '').strip()
        system_code = (asset.system_asset_no or '').strip().upper()
        primary_code = (asset.asset_code or '').strip().upper()
        match = re.fullmatch(r'AST-(\d+)', system_code)
        if match:
            highest_system_number = max(highest_system_number, int(match.group(1)))

        asset.domain_id = domain.id
        asset.code_source = 'admin_assigned' if primary_code else 'pending_admin'
        asset.sn_status = 'provided' if (asset.serial_number or '').strip() else 'pending'
        asset.label_version = 2
        asset.qr_value = f'WMS2:A:{system_code}'
        asset.save(update_fields=[
            'domain', 'code_source', 'sn_status', 'label_version', 'qr_value', 'updated_at',
        ])

        if system_code:
            AssetIdentifier.objects.get_or_create(
                identifier_type='system', normalized_value=system_code,
                defaults={
                    'asset_id': asset.id, 'value': system_code, 'issuer': '售后仓库系统',
                },
            )
        if primary_code:
            AssetIdentifier.objects.get_or_create(
                identifier_type='admin', normalized_value=primary_code,
                defaults={
                    'asset_id': asset.id, 'value': asset.asset_code, 'issuer': '公司行政/ERP',
                    'is_primary': True,
                },
            )
        serial_number = (asset.serial_number or '').strip()
        if serial_number:
            AssetIdentifier.objects.create(
                asset_id=asset.id, identifier_type='manufacturer_sn', value=serial_number,
                normalized_value=serial_number.upper(), issuer=asset.manufacturer or '',
            )
        manufacturer_barcode = (asset.manufacturer_barcode or '').strip()
        if manufacturer_barcode:
            AssetIdentifier.objects.create(
                asset_id=asset.id, identifier_type='manufacturer_barcode', value=manufacturer_barcode,
                normalized_value=manufacturer_barcode.upper(), issuer=asset.manufacturer or '',
            )
        if old_qr and old_qr.upper() not in {system_code, f'WMS2:A:{system_code}'}:
            AssetIdentifier.objects.create(
                asset_id=asset.id, identifier_type='other', value=old_qr,
                normalized_value=old_qr.upper(), issuer='旧版系统二维码', is_active=True,
            )

    AssetCodeSequence.objects.update_or_create(
        scheme='system', prefix='AST',
        defaults={'last_number': highest_system_number, 'width': 8, 'is_exhausted': False},
    )


def remove_seeded_domains(apps, schema_editor):
    Asset = apps.get_model('inventory', 'Asset')
    InventoryDomain = apps.get_model('inventory', 'InventoryDomain')
    Asset.objects.update(domain=None)
    InventoryDomain.objects.filter(code__in=['MARKET', 'RND']).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0022_fix_assembled_asset_availability'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='InventoryDomain',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('code', models.CharField(max_length=16, unique=True, verbose_name='归属域代码')),
                ('name', models.CharField(max_length=80, verbose_name='归属域名称')),
                ('code_scheme', models.CharField(choices=[('market_generated', '市场编码（系统生成）'), ('admin_assigned', '研发行政编码')], max_length=24, verbose_name='编码方式')),
                ('is_active', models.BooleanField(default=True, verbose_name='是否启用')),
            ],
            options={'verbose_name': '资产归属域', 'verbose_name_plural': '资产归属域', 'ordering': ['code']},
        ),
        migrations.CreateModel(
            name='Manufacturer',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('code', models.CharField(max_length=80, unique=True, verbose_name='编码')),
                ('name', models.CharField(max_length=160, verbose_name='名称')),
                ('contact_name', models.CharField(blank=True, default='', max_length=120, verbose_name='联系人')),
                ('phone', models.CharField(blank=True, default='', max_length=32, verbose_name='联系电话')),
                ('notes', models.TextField(blank=True, default='', verbose_name='备注')),
                ('is_active', models.BooleanField(default=True, verbose_name='是否启用')),
            ],
            options={'verbose_name': '生产厂家资料', 'verbose_name_plural': '生产厂家资料', 'ordering': ['name', 'code']},
        ),
        migrations.AddField(model_name='asset', name='code_source', field=models.CharField(choices=[('market_generated', '系统生成市场编码'), ('admin_assigned', '行政编码'), ('pending_admin', '待行政赋码')], default='pending_admin', max_length=24, verbose_name='主资产编码来源')),
        migrations.AddField(model_name='asset', name='label_version', field=models.PositiveSmallIntegerField(default=2, verbose_name='标签协议版本')),
        migrations.AddField(model_name='asset', name='project', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assets', to='inventory.project', verbose_name='所属项目')),
        migrations.AddField(model_name='asset', name='sn_status', field=models.CharField(choices=[('provided', '已提供'), ('no_sn', '厂家无 SN'), ('pending', '暂未取得'), ('unreadable', '标签无法识别'), ('manual', '人工补充')], default='pending', max_length=16, verbose_name='SN 状态')),
        migrations.AddField(model_name='asset', name='work_order', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assets', to='inventory.workorder', verbose_name='关联工单')),
        migrations.CreateModel(
            name='AssetCodeSequence',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('scheme', models.CharField(choices=[('system', '系统永久编号'), ('market', '市场主资产编码')], max_length=16, verbose_name='编码体系')),
                ('prefix', models.CharField(max_length=80, verbose_name='编码前缀')),
                ('last_number', models.PositiveBigIntegerField(default=0, verbose_name='最后流水号')),
                ('width', models.PositiveSmallIntegerField(default=4, verbose_name='流水位数')),
                ('is_exhausted', models.BooleanField(default=False, verbose_name='是否已用尽')),
            ],
            options={'verbose_name': '资产编码流水', 'verbose_name_plural': '资产编码流水', 'ordering': ['scheme', 'prefix'], 'constraints': [models.UniqueConstraint(fields=('scheme', 'prefix'), name='unique_asset_code_sequence')]},
        ),
        migrations.CreateModel(
            name='AssetIdentifier',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('identifier_type', models.CharField(choices=[('system', '系统永久编号'), ('market', '市场主资产编码'), ('admin', '行政资产编码'), ('manufacturer_sn', '生产厂家 SN'), ('manufacturer_barcode', '生产厂家条码'), ('legacy', '历史编码'), ('other', '其他标识')], max_length=32, verbose_name='标识类型')),
                ('value', models.CharField(max_length=255, verbose_name='标识原文')),
                ('normalized_value', models.CharField(db_index=True, max_length=255, verbose_name='标准化标识')),
                ('is_primary', models.BooleanField(default=False, verbose_name='是否当前主资产编码')),
                ('is_active', models.BooleanField(default=True, verbose_name='是否有效')),
                ('issuer', models.CharField(blank=True, default='', max_length=120, verbose_name='签发方')),
                ('valid_from', models.DateTimeField(default=django.utils.timezone.now, verbose_name='生效时间')),
                ('valid_until', models.DateTimeField(blank=True, null=True, verbose_name='失效时间')),
                ('change_reason', models.TextField(blank=True, default='', verbose_name='变更原因')),
                ('asset', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='identifiers', to='inventory.asset')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_asset_identifiers', to=settings.AUTH_USER_MODEL, verbose_name='操作人')),
            ],
            options={'verbose_name': '资产识别码', 'verbose_name_plural': '资产识别码', 'ordering': ['asset__system_asset_no', '-is_primary', 'identifier_type', '-created_at']},
        ),
        migrations.CreateModel(
            name='AssetTypeCode',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('code', models.CharField(max_length=12, unique=True, verbose_name='设备类型代码')),
                ('name', models.CharField(max_length=80, verbose_name='设备类型名称')),
                ('description', models.TextField(blank=True, default='', verbose_name='说明')),
                ('is_active', models.BooleanField(default=True, verbose_name='是否启用')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_asset_type_codes', to=settings.AUTH_USER_MODEL, verbose_name='创建人')),
            ], options={'verbose_name': '市场设备类型代码', 'verbose_name_plural': '市场设备类型代码', 'ordering': ['code']},
        ),
        migrations.AddField(model_name='asset', name='asset_type_code', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='assets', to='inventory.assettypecode', verbose_name='设备类型代码')),
        migrations.CreateModel(
            name='AssetDomainChange',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('reason', models.TextField(verbose_name='调整原因')), ('changed_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='生效时间')),
                ('approved_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='approved_asset_domain_changes', to=settings.AUTH_USER_MODEL, verbose_name='确认人')),
                ('asset', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='domain_changes', to='inventory.asset')),
                ('requested_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='requested_asset_domain_changes', to=settings.AUTH_USER_MODEL, verbose_name='操作人')),
                ('new_primary_identifier', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='domain_changes_as_new', to='inventory.assetidentifier', verbose_name='新主资产编码')),
                ('old_primary_identifier', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='domain_changes_as_old', to='inventory.assetidentifier', verbose_name='原主资产编码')),
                ('from_domain', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='domain_changes_from', to='inventory.inventorydomain', verbose_name='原归属域')),
                ('to_domain', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='domain_changes_to', to='inventory.inventorydomain', verbose_name='新归属域')),
            ], options={'verbose_name': '资产归属调整记录', 'verbose_name_plural': '资产归属调整记录', 'ordering': ['-changed_at']},
        ),
        migrations.AddField(model_name='asset', name='domain', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='assets', to='inventory.inventorydomain', verbose_name='资产归属域')),
        migrations.AddField(model_name='asset', name='manufacturer_ref', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assets', to='inventory.manufacturer', verbose_name='生产厂家资料')),
        migrations.CreateModel(
            name='ProductModelCode',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('code', models.CharField(max_length=24, verbose_name='型号代码')), ('name', models.CharField(max_length=120, verbose_name='型号名称')),
                ('description', models.TextField(blank=True, default='', verbose_name='说明')), ('is_active', models.BooleanField(default=True, verbose_name='是否启用')),
                ('asset_type_code', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='product_models', to='inventory.assettypecode', verbose_name='设备类型代码')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_product_model_codes', to=settings.AUTH_USER_MODEL, verbose_name='创建人')),
                ('manufacturer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='product_models', to='inventory.manufacturer', verbose_name='生产厂家')),
            ], options={'verbose_name': '产品型号代码', 'verbose_name_plural': '产品型号代码', 'ordering': ['asset_type_code__code', 'code']},
        ),
        migrations.AddField(model_name='asset', name='product_model_code', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='assets', to='inventory.productmodelcode', verbose_name='型号代码')),
        migrations.CreateModel(
            name='AssetAssemblyLink',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('installed_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='装入时间')),
                ('removed_at', models.DateTimeField(blank=True, null=True, verbose_name='拆出时间')),
                ('note', models.CharField(blank=True, default='', max_length=255, verbose_name='备注')),
                ('child_asset', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='assembly_history', to='inventory.asset', verbose_name='子资产/组件')),
                ('operator', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='asset_assembly_operations', to=settings.AUTH_USER_MODEL, verbose_name='操作人')),
                ('parent_asset', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='installed_components', to='inventory.asset', verbose_name='父资产/整机')),
                ('work_order', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='asset_assembly_links', to='inventory.workorder', verbose_name='关联工单')),
            ],
            options={'verbose_name': '资产装配履历', 'verbose_name_plural': '资产装配履历', 'ordering': ['-installed_at'], 'constraints': [models.CheckConstraint(condition=~models.Q(parent_asset=models.F('child_asset')), name='asset_assembly_parent_not_child'), models.UniqueConstraint(condition=models.Q(removed_at__isnull=True), fields=('child_asset',), name='unique_active_asset_assembly_child')]},
        ),
        migrations.AddConstraint(model_name='assetidentifier', constraint=models.UniqueConstraint(condition=models.Q(identifier_type='system'), fields=('asset',), name='unique_system_identifier_per_asset')),
        migrations.AddConstraint(model_name='assetidentifier', constraint=models.UniqueConstraint(condition=models.Q(is_active=True, is_primary=True), fields=('asset',), name='unique_active_primary_identifier_per_asset')),
        migrations.AddConstraint(model_name='assetidentifier', constraint=models.UniqueConstraint(condition=models.Q(identifier_type__in=['system', 'market', 'admin', 'legacy']), fields=('normalized_value',), name='unique_controlled_asset_identifier_value')),
        migrations.AddConstraint(model_name='productmodelcode', constraint=models.UniqueConstraint(fields=('asset_type_code', 'code'), name='unique_product_model_code_per_asset_type')),
        migrations.RunPython(seed_asset_identity, remove_seeded_domains),
    ]
