"""清理过期的认证限流状态和 API 令牌元数据。"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.common.models import ApiAccessToken, ApiThrottleState, LoginThrottleState, SecurityAuditEvent


class Command(BaseCommand):
    help = '清理过期登录限流、接口限流状态和长期失效的 API 令牌。'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='只统计待清理数量，不删除数据。',
        )

    def handle(self, *args, **options):
        now = timezone.now()
        retention_seconds = max(86400, settings.SECURITY_STATE_RETENTION_SECONDS)
        cutoff = now - timedelta(seconds=retention_seconds)

        login_states = LoginThrottleState.objects.filter(updated_at__lt=cutoff)
        api_states = ApiThrottleState.objects.filter(updated_at__lt=cutoff)
        expired_tokens = ApiAccessToken.objects.filter(
            expires_at__lt=cutoff,
        )
        revoked_tokens = ApiAccessToken.objects.filter(
            revoked_at__isnull=False,
            revoked_at__lt=cutoff,
        )
        # union 复合子查询不允许携带 ORDER BY（SQLite 会直接报错），两侧都需清除默认排序。
        token_ids = expired_tokens.order_by().values('pk').union(
            revoked_tokens.order_by().values('pk')
        )
        token_count = ApiAccessToken.objects.filter(pk__in=token_ids).count()
        security_events = SecurityAuditEvent.objects.filter(created_at__lt=cutoff)
        counts = {
            'login_states': login_states.count(),
            'api_states': api_states.count(),
            'tokens': token_count,
            'security_events': security_events.count(),
        }

        mode = '预览' if options['dry_run'] else '清理'
        self.stdout.write(
            f'{mode}安全状态：登录限流 {counts["login_states"]} 条，'
            f'接口限流 {counts["api_states"]} 条，失效令牌 {counts["tokens"]} 条，'
            f'安全事件 {counts["security_events"]} 条。'
        )
        if options['dry_run']:
            return

        login_deleted, _ = login_states.delete()
        api_deleted, _ = api_states.delete()
        token_deleted, _ = ApiAccessToken.objects.filter(pk__in=token_ids).delete()
        event_deleted, _ = security_events.delete()
        self.stdout.write(self.style.SUCCESS(
            f'安全状态清理完成：登录限流 {login_deleted} 条，'
            f'接口限流 {api_deleted} 条，失效令牌 {token_deleted} 条，'
            f'安全事件 {event_deleted} 条。'
        ))
