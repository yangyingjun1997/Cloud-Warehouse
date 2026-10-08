import uuid

# Generated manually for the warehouse project.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('accounts', '0001_initial'),
        ('inventory', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='WorkflowRequest',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('request_no', models.CharField(default='', max_length=80, unique=True)),
                ('request_type', models.CharField(choices=[('borrow', '借用'), ('return', '归还'), ('issue', '领用'), ('transfer', '调拨'), ('repair', '维修'), ('damage', '报损')], max_length=32)),
                ('status', models.CharField(choices=[('draft', '草稿'), ('pending', '待审批'), ('rejected', '已驳回'), ('approved', '已通过'), ('waiting_warehouse', '待仓库处理'), ('done', '已完成'), ('canceled', '已取消'), ('closed', '异常关闭')], default='draft', max_length=32)),
                ('reason', models.TextField(blank=True, default='')),
                ('expected_return_date', models.DateField(blank=True, null=True)),
                ('applicant', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='workflow_requests', to=settings.AUTH_USER_MODEL)),
                ('department', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='accounts.department')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='WorkflowRequestLine',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('quantity', models.PositiveIntegerField(default=1)),
                ('note', models.CharField(blank=True, default='', max_length=255)),
                ('asset', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='inventory.asset')),
                ('request', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lines', to='workflow.workflowrequest')),
                ('stock_item', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='inventory.stockitem')),
            ],
        ),
        migrations.CreateModel(
            name='ApprovalTask',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('pending', '待处理'), ('approved', '已通过'), ('rejected', '已驳回'), ('canceled', '已取消')], default='pending', max_length=32)),
                ('comment', models.TextField(blank=True, default='')),
                ('acted_at', models.DateTimeField(blank=True, null=True)),
                ('approver', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='approval_tasks', to=settings.AUTH_USER_MODEL)),
                ('request', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='approval_tasks', to='workflow.workflowrequest')),
            ],
        ),
        migrations.CreateModel(
            name='ApprovalLog',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('action', models.CharField(max_length=50)),
                ('comment', models.TextField(blank=True, default='')),
                ('actor', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('request', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='approval_logs', to='workflow.workflowrequest')),
            ],
        ),
    ]
