import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0028_alter_importbatch_status_importmutation'),
    ]

    operations = [
        migrations.AddField(
            model_name='asset',
            name='customer_ref',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='assets',
                to='inventory.customer',
                verbose_name='客户',
            ),
        ),
    ]