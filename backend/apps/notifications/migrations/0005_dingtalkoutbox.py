from django.db import migrations, models
import apps.common.models


class Migration(migrations.Migration):
    dependencies = [('notifications', '0004_alter_lowstockalert_source_type')]

    operations = [
        migrations.CreateModel(
            name='DingTalkOutbox',
            fields=[
                ('id', models.UUIDField(default=apps.common.models.uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('idempotency_key', models.CharField(max_length=64, unique=True)),
                ('delivery_mode', models.CharField(choices=[('personal', '个人工作通知'), ('users_or_group', '个人通知，失败时群通知'), ('group', '群机器人')], default='users_or_group', max_length=24)),
                ('recipient_user_ids', models.JSONField(blank=True, default=list)),
                ('title', models.CharField(max_length=160)),
                ('content', models.TextField()),
                ('status', models.CharField(choices=[('pending', '待发送'), ('sending', '发送中'), ('delivered', '已送达'), ('failed', '已失败')], default='pending', max_length=16)),
                ('attempt_count', models.PositiveSmallIntegerField(default=0)),
                ('next_attempt_at', models.DateTimeField()),
                ('locked_at', models.DateTimeField(blank=True, null=True)),
                ('delivered_at', models.DateTimeField(blank=True, null=True)),
                ('last_error', models.CharField(blank=True, default='', max_length=500)),
            ],
            options={
                'verbose_name': '钉钉发送队列',
                'verbose_name_plural': '钉钉发送队列',
                'ordering': ['next_attempt_at', 'created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='dingtalkoutbox',
            index=models.Index(fields=['status', 'next_attempt_at'], name='notify_outbox_due_idx'),
        ),
    ]
