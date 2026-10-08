import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0012_inventorychecktask_reopen_note_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='importbatch',
            name='mode',
            field=models.CharField(
                choices=[('incremental', '增量导入'), ('baseline', '全量基准导入')],
                default='incremental',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='asset',
            name='status',
            field=models.CharField(
                choices=[
                    ('in_stock', '在库'), ('borrowed', '已借出'), ('issued', '已领用'),
                    ('pending_out', '待出库'), ('pending_return', '待归还验收'),
                    ('pending_inspection', '待质检'), ('maintenance', '待维修'),
                    ('repairing', '维修中'), ('sold', '已售出'),
                    ('returned_to_vendor', '已退供应商'), ('scrapped', '已报废'),
                    ('damaged', '已报损'), ('lost', '已丢失'), ('disabled', '已停用'),
                    ('converted_to_stock', '已转为耗材配件'),
                ],
                default='in_stock',
                max_length=32,
            ),
        ),
        migrations.AlterModelOptions(
            name='stockitem',
            options={'ordering': ['name'], 'verbose_name': '耗材配件', 'verbose_name_plural': '耗材配件'},
        ),
        migrations.CreateModel(
            name='InventoryConversion',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('conversion_no', models.CharField(max_length=80, unique=True)),
                ('direction', models.CharField(choices=[('stock_to_asset', '耗材配件转单件资产'), ('asset_to_stock', '单件资产转耗材配件')], max_length=24)),
                ('quantity', models.PositiveIntegerField()),
                ('note', models.CharField(blank=True, default='', max_length=255)),
                ('assets', models.ManyToManyField(related_name='inventory_conversions', to='inventory.asset')),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='inventory_conversions', to=settings.AUTH_USER_MODEL)),
                ('item_type', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='inventory_conversions', to='inventory.itemtype')),
                ('stock_item', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='inventory_conversions', to='inventory.stockitem')),
            ],
            options={
                'verbose_name': '库存管理方式转换记录',
                'verbose_name_plural': '库存管理方式转换记录',
                'ordering': ['-created_at'],
            },
        ),
    ]
