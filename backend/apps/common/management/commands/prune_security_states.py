"""清理过期的认证限流状态和 API 令牌元数据。"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import AccountSession
from apps.common.models import ApiAccessToken, ApiThrottleState, LoginThrottleState


class Command(BaseCommand):
    help = '清理过期登录限流、账号会话、接口限流状态和长期失效的 API 令牌。'

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
        # 长期令牌的 expires_at 为空，必须排除，否则这里会把它当成"过期"删掉。
        expired_tokens = ApiAccessToken.objects.filter(
            expires_at__isnull=False,
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
        live_session_keys = Session.objects.filter(expire_date__gt=now).values('session_key')
        revoked_account_sessions = AccountSession.objects.filter(revoked_at__lt=cutoff)
        inactive_account_sessions = AccountSession.objects.filter(updated_at__lt=cutoff).exclude(
            session_key__in=live_session_keys,
        )
        account_session_ids = revoked_account_sessions.order_by().values('pk').union(
            inactive_account_sessions.order_by().values('pk')
        )
        account_session_count = AccountSession.objects.filter(pk__in=account_session_ids).count()
        counts = {
            'login_states': login_states.count(),
            'account_sessions': account_session_count,
            'api_states': api_states.count(),
            'tokens': token_count,
        }

        mode = '预览' if options['dry_run'] else '清理'
        self.stdout.write(
            f'{mode}安全状态：登录限流 {counts["login_states"]} 条，'
            f'过期设备会话 {counts["account_sessions"]} 条，'
            f'接口限流 {counts["api_states"]} 条，失效令牌 {counts["tokens"]} 条。'
        )
        if options['dry_run']:
            return

        login_deleted, _ = login_states.delete()
        # 理论上撤销时已删除 Django Session；这里再清理一次，避免异常中断留下可用会话。
        Session.objects.filter(
            session_key__in=revoked_account_sessions.values('session_key'),
        ).delete()
        account_session_deleted, _ = AccountSession.objects.filter(pk__in=account_session_ids).delete()
        api_deleted, _ = api_states.delete()
        token_deleted, _ = ApiAccessToken.objects.filter(pk__in=token_ids).delete()
        self.stdout.write(self.style.SUCCESS(
            f'安全状态清理完成：登录限流 {login_deleted} 条，'
            f'过期设备会话 {account_session_deleted} 条，'
            f'接口限流 {api_deleted} 条，失效令牌 {token_deleted} 条。'
        ))
