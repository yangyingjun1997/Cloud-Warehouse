import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('notifications', '0002_alter_notification_options'),
    ]

    operations = [
        migrations.CreateModel(
            name='LowStockAlert',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('source_type', models.CharField(choices=[('stock_item', '数量物料'), ('item_type', '单件资产类型')], max_length=20)),
                ('source_id', models.UUIDField()),
                ('source_name', models.CharField(max_length=160)),
                ('current_quantity', models.PositiveIntegerField(default=0)),
                ('safety_stock', models.PositiveIntegerField(default=0)),
                ('is_active', models.BooleanField(default=False)),
                ('active_since', models.DateTimeField(blank=True, null=True)),
                ('last_notified_at', models.DateTimeField(blank=True, null=True)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'verbose_name': '安全库存预警状态',
                'verbose_name_plural': '安全库存预警状态',
                'ordering': ['-is_active', 'source_type', 'source_name'],
            },
        ),
        migrations.AddConstraint(
            model_name='lowstockalert',
            constraint=models.UniqueConstraint(fields=('source_type', 'source_id'), name='unique_low_stock_alert_source'),
        ),
    ]
