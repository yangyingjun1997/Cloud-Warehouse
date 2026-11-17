import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('accounts', '0004_accountsession'),
    ]

    operations = [
        migrations.CreateModel(
            name='AccountRequest',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('real_name', models.CharField(max_length=50, verbose_name='姓名')),
                ('employee_no', models.CharField(max_length=50, verbose_name='员工工号')),
                ('department_text', models.CharField(blank=True, default='', max_length=100, verbose_name='部门')),
                ('phone', models.CharField(max_length=32, verbose_name='联系电话')),
                ('reason', models.TextField(blank=True, default='', verbose_name='申请说明')),
                ('status', models.CharField(choices=[('pending', '待处理'), ('created', '已建号'), ('rejected', '已驳回')], default='pending', max_length=16, verbose_name='处理状态')),
                ('handled_at', models.DateTimeField(blank=True, null=True, verbose_name='处理时间')),
                ('handle_note', models.CharField(blank=True, default='', max_length=255, verbose_name='处理说明')),
                ('created_user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_from_account_requests', to=settings.AUTH_USER_MODEL, verbose_name='建号结果')),
                ('handled_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='handled_account_requests', to=settings.AUTH_USER_MODEL, verbose_name='处理人')),
            ],
            options={
                'verbose_name': '账号开通申请',
                'verbose_name_plural': '账号开通申请',
                'ordering': ['-created_at'],
                'constraints': [models.UniqueConstraint(condition=models.Q(('status', 'pending')), fields=('employee_no',), name='unique_pending_account_request_per_employee')],
            },
        ),
    ]
