from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('common', '0015_audit_actor_snapshot'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='domain_expiry_date',
            field=models.DateField(
                blank=True,
                help_text='填写后，到期前 30 天会在页面顶部提醒续费。',
                null=True,
                verbose_name='域名到期日',
            ),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='domain_name',
            field=models.CharField(blank=True, default='', max_length=120, verbose_name='域名'),
        ),
    ]
