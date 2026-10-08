import uuid

# Generated manually for the warehouse project.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Notification',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('title', models.CharField(max_length=120)),
                ('content', models.TextField()),
                ('level', models.CharField(choices=[('info', '信息'), ('success', '成功'), ('warning', '警告'), ('danger', '危险')], default='info', max_length=16)),
                ('is_read', models.BooleanField(default=False)),
                ('related_model', models.CharField(blank=True, default='', max_length=120)),
                ('related_object_id', models.CharField(blank=True, default='', max_length=64)),
                ('recipient', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notifications', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
