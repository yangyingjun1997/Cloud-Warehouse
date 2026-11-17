import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('workflow', '0019_backfill_active_asset_reservations'),
    ]

    operations = [
        migrations.AddField(
            model_name='workflowrequest',
            name='is_external_recipient',
            field=models.BooleanField(default=False, help_text='勾选后接收人只记录姓名，不关联系统账号，需要现场核对身份。', verbose_name='接收人为非系统用户'),
        ),
        migrations.AddField(
            model_name='workflowrequest',
            name='recipient',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='received_workflow_requests', to=settings.AUTH_USER_MODEL, verbose_name='接收人账号'),
        ),
    ]
