from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('notifications', '0003_lowstockalert')]

    operations = [
        migrations.AlterField(
            model_name='lowstockalert',
            name='source_type',
            field=models.CharField(
                choices=[('stock_item', '耗材配件'), ('item_type', '单件资产类型')],
                max_length=20,
            ),
        ),
    ]
