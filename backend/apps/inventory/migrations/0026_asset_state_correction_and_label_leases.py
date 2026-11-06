import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0025_asset_label_print_job'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='assetlabelprintjob',
            name='attempt_count',
            field=models.PositiveIntegerField(default=0, verbose_name='尝试次数'),
        ),
        migrations.AddField(
            model_name='assetlabelprintjob',
            name='claim_expires_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='领取租约到期时间'),
        ),
        migrations.AddField(
            model_name='assetlabelprintjob',
            name='claim_token_hash',
            field=models.CharField(blank=True, default='', max_length=64, verbose_name='领取令牌摘要'),
        ),
        migrations.AddField(
            model_name='assetlabelprintjob',
            name='claimed_by_user',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='claimed_asset_label_print_jobs',
                to=settings.AUTH_USER_MODEL, verbose_name='领取账号',
            ),
        ),
        migrations.AddField(
            model_name='assetlabelprintjob',
            name='last_heartbeat_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='最后心跳时间'),
        ),
        migrations.AddField(
            model_name='maintenancerecord',
            name='reported_date',
            field=models.DateField(blank=True, null=True, verbose_name='故障/维修发生日期'),
        ),
        migrations.AddField(
            model_name='maintenancerecord',
            name='source',
            field=models.CharField(
                choices=[
                    ('workflow', '申请流程'),
                    ('manual', '人工新建'),
                    ('legacy_import', '历史台账补录'),
                ],
                default='manual', max_length=20, verbose_name='来源',
            ),
        ),
        migrations.AlterField(
            model_name='asset',
            name='code_source',
            field=models.CharField(
                choices=[
                    ('warehouse_assigned', '仓管既有编码'),
                    ('market_generated', '系统生成市场编码'),
                    ('admin_assigned', '行政编码'),
                    ('pending_admin', '待赋码'),
                ],
                default='pending_admin', max_length=24, verbose_name='主资产编码来源',
            ),
        ),
        migrations.AlterField(
            model_name='assetlifecycleevent',
            name='event_type',
            field=models.CharField(
                choices=[
                    ('maintenance', '维修'),
                    ('handover', '交接'),
                    ('attachment', '附件归档'),
                    ('scrap_request', '报废申请'),
                    ('note', '其他记录'),
                    ('correction', '台账校正'),
                ],
                max_length=24,
            ),
        ),
        migrations.CreateModel(
            name='AssetStateCorrection',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('from_status', models.CharField(
                    choices=[
                        ('in_stock', '在库'), ('borrowed', '已借出'), ('issued', '已领用'),
                        ('pending_out', '待出库'), ('pending_return', '待归还验收'),
                        ('pending_inspection', '待质检'), ('maintenance', '待维修'),
                        ('repairing', '维修中'), ('sold', '已售出'),
                        ('internal_borrow', '内部借用库'), ('shipped_out', '已出库'),
                        ('assembled', '已组装'), ('returned_to_vendor', '已退供应商'),
                        ('scrapped', '已报废'), ('damaged', '已报损'), ('lost', '已丢失'),
                        ('disabled', '已停用'), ('converted_to_stock', '已转为耗材配件'),
                    ],
                    max_length=32, verbose_name='校正前状态',
                )),
                ('to_status', models.CharField(
                    choices=[
                        ('in_stock', '在库'), ('borrowed', '已借出'), ('issued', '已领用'),
                        ('pending_out', '待出库'), ('pending_return', '待归还验收'),
                        ('pending_inspection', '待质检'), ('maintenance', '待维修'),
                        ('repairing', '维修中'), ('sold', '已售出'),
                        ('internal_borrow', '内部借用库'), ('shipped_out', '已出库'),
                        ('assembled', '已组装'), ('returned_to_vendor', '已退供应商'),
                        ('scrapped', '已报废'), ('damaged', '已报损'), ('lost', '已丢失'),
                        ('disabled', '已停用'), ('converted_to_stock', '已转为耗材配件'),
                    ],
                    max_length=32, verbose_name='校正后状态',
                )),
                ('before_snapshot', models.JSONField(default=dict, verbose_name='校正前快照')),
                ('after_snapshot', models.JSONField(default=dict, verbose_name='校正后快照')),
                ('reason', models.TextField(verbose_name='校正原因')),
                ('source', models.CharField(
                    choices=[('manual', '普通校正'), ('legacy_review', '历史数据核对')],
                    default='manual', max_length=20, verbose_name='来源',
                )),
                ('actor', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='asset_state_corrections',
                    to=settings.AUTH_USER_MODEL, verbose_name='操作人',
                )),
                ('asset', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='state_corrections', to='inventory.asset',
                )),
                ('target_location', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    to='inventory.location', verbose_name='目标库位',
                )),
                ('target_warehouse', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    to='inventory.warehouse', verbose_name='目标仓库',
                )),
                ('verified_by', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='verified_asset_state_corrections',
                    to=settings.AUTH_USER_MODEL, verbose_name='复核人',
                )),
            ],
            options={
                'verbose_name': '资产台账校正',
                'verbose_name_plural': '资产台账校正',
                'ordering': ['-created_at'],
                'indexes': [models.Index(
                    fields=['asset', 'created_at'],
                    name='inv_corr_asset_created_idx',
                )],
            },
        ),
    ]
