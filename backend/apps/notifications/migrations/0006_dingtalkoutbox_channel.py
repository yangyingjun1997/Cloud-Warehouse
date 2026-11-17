from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0005_dingtalkoutbox'),
    ]

    operations = [
        migrations.AddField(
            model_name='dingtalkoutbox',
            name='channel',
            field=models.CharField(
                blank=True, default='default', max_length=24, verbose_name='通知渠道',
            ),
        ),
    ]
