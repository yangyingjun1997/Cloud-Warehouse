"""为申请单增加供应商外键（退货给供应商用）。

背景：退货给供应商此前没有任何专属字段，用户只能把供应商名塞进
「接收人单位」文本，既无法按供应商对账，也无法追溯退给了谁。
本迁移与 customer_ref 同构地增加 supplier_ref 外键。

手工生成原因：makemigrations 在本机被文件锁/安全软件拦截，
无法写入新文件，因此按 Django 标准格式手写，字段定义与 models.py 一致。
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workflow', '0020_workflowrequest_recipient_and_external_flag'),
        ('inventory', '0031_location_is_active'),
    ]

    operations = [
        migrations.AddField(
            model_name='workflowrequest',
            name='supplier_ref',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='workflow_requests',
                to='inventory.supplier',
                verbose_name='供应商',
            ),
        ),
    ]
