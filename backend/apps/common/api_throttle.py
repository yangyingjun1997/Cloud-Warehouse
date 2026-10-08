"""不依赖进程内缓存的匿名 API 请求限流。"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .auth_throttle import _digest, get_client_ip
from .models import ApiThrottleState


def _state_key(prefix: str, value: str) -> str:
    return f'api-{prefix}:{_digest(value or "unknown")}'


def api_ip_key(client_ip: str) -> str:
    return _state_key('ip', client_ip)


def api_account_key(username: str) -> str | None:
    username = (username or '').strip()
    return _state_key('account', username) if username else None


def _limits() -> tuple[int, int, int]:
    return (
        max(1, settings.API_RATE_LIMIT_IP_REQUESTS),
        max(1, settings.API_RATE_LIMIT_ACCOUNT_REQUESTS),
        max(1, settings.API_RATE_LIMIT_WINDOW_SECONDS),
    )


def _consume_one(key: str, limit: int, now, window_seconds: int) -> tuple[bool, int]:
    state, _ = ApiThrottleState.objects.select_for_update().get_or_create(
        key=key,
        defaults={'window_started_at': now},
    )
    window_end = state.window_started_at + timedelta(seconds=window_seconds)
    if now >= window_end:
        state.window_started_at = now
        state.request_count = 0
        window_end = now + timedelta(seconds=window_seconds)
    if state.request_count >= limit:
        return False, max(1, int((window_end - now).total_seconds()))

    state.request_count += 1
    state.save(update_fields=['request_count', 'window_started_at', 'updated_at'])
    return True, max(1, int((window_end - now).total_seconds()))


def consume_token_request(*, username: str, client_ip: str) -> tuple[bool, int]:
    """消费一次令牌请求额度，返回 (是否允许, 建议等待秒数)。"""

    ip_limit, account_limit, window_seconds = _limits()
    now = timezone.now()
    keys = [(api_ip_key(client_ip), ip_limit)]
    account_key = api_account_key(username)
    if account_key:
        keys.append((account_key, account_limit))

    with transaction.atomic():
        waits = []
        allowed = True
        for key, limit in keys:
            current_allowed, wait = _consume_one(key, limit, now, window_seconds)
            allowed = allowed and current_allowed
            waits.append(wait)
        return allowed, max(waits)


def client_ip_from_request(request) -> str:
    return get_client_ip(request)
