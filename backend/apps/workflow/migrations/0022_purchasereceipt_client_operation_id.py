from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workflow', '0021_workflowrequest_supplier_ref'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchasereceipt',
            name='client_operation_id',
            field=models.UUIDField(
                blank=True,
                db_index=True,
                editable=False,
                null=True,
                verbose_name='客户端操作编号',
            ),
        ),
        migrations.AddConstraint(
            model_name='purchasereceipt',
            constraint=models.UniqueConstraint(
                condition=models.Q(('client_operation_id__isnull', False)),
                fields=('order', 'client_operation_id'),
                name='unique_purchase_receipt_client_operation',
            ),
        ),
    ]
