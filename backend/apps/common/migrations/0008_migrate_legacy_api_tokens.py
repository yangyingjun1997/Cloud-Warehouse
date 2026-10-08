"""把 DRF 旧版明文令牌迁移为可过期的哈希令牌。"""

import hashlib
from datetime import timedelta

from django.conf import settings
from django.db import migrations
from django.utils import timezone


def migrate_legacy_tokens(apps, schema_editor):
    legacy_model = apps.get_model('authtoken', 'Token')
    access_token_model = apps.get_model('common', 'ApiAccessToken')
    expires_at = timezone.now() + timedelta(
        seconds=max(60, int(getattr(settings, 'API_TOKEN_TTL_SECONDS', 2592000))),
    )

    for legacy in legacy_model.objects.all().iterator():
        token_hash = hashlib.sha256(legacy.key.encode('utf-8')).hexdigest()
        access_token_model.objects.get_or_create(
            token_hash=token_hash,
            defaults={
                'user_id': legacy.user_id,
                'token_prefix': legacy.key[:12],
                'expires_at': expires_at,
            },
        )
    # 旧模型将不再参与认证，删除其中的明文令牌，避免继续保留敏感凭据。
    legacy_model.objects.all().delete()


def reverse_migration(apps, schema_editor):
    # 无法从哈希恢复令牌原文，回滚时保留新模型数据，不重新生成旧令牌。
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('authtoken', '0004_alter_tokenproxy_options'),
        ('common', '0007_apiaccesstoken'),
    ]

    operations = [
        migrations.RunPython(migrate_legacy_tokens, reverse_migration),
    ]
