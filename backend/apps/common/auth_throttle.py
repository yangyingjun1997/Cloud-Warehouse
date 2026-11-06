"""基于数据库的登录失败限流。

状态按账号和客户端 IP 分开统计。账号/IP 均只以 SHA-256 摘要落库，避免
把可直接识别的信息写入限流表；实际账号认证仍交给 Django 的认证后端。
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import LoginThrottleState


def _trusted_proxies() -> set[str]:
    """返回允许提供转发头的反向代理地址。

    生产环境由 nginx 反代，REMOTE_ADDR 恒为代理地址，必须读转发头才能
    拿到真实来源；但若无条件采信，攻击者每次换一个 X-Forwarded-For 就能
    绕过 IP 维度限流。所以这里改成白名单：只有 REMOTE_ADDR 命中配置的
    代理地址时才解析转发头。
    """

    raw = getattr(settings, 'TRUSTED_PROXY_IPS', ()) or ()
    if isinstance(raw, str):
        raw = raw.split(',')
    return {str(item).strip() for item in raw if str(item).strip()}


def get_client_ip(request) -> str:
    """获取客户端地址，不可信来源一律回退到 REMOTE_ADDR。"""

    remote_addr = (request.META.get('REMOTE_ADDR') or '').strip()[:128] or 'unknown'
    if not _trusted_proxies():
        # 未配置代理白名单：直连部署，转发头完全不可信。
        return remote_addr
    if remote_addr not in _trusted_proxies():
        return remote_addr
    # 已确认来自受信代理。X-Real-IP 由 nginx 覆盖写入，优先使用。
    real_ip = (request.META.get('HTTP_X_REAL_IP') or '').strip()
    if real_ip:
        return real_ip.split(',')[0].strip()[:128]
    forwarded = (request.META.get('HTTP_X_FORWARDED_FOR') or '').strip()
    if forwarded:
        # 取最右一跳：左侧条目客户端可以任意伪造，最右侧才是代理追加的真实来源。
        candidate = forwarded.split(',')[-1].strip()
        if candidate:
            return candidate[:128]
    return remote_addr


def _digest(value: str) -> str:
    return hashlib.sha256(value.casefold().encode('utf-8')).hexdigest()


def account_key(username: str) -> str | None:
    username = (username or '').strip()
    return f'account:{_digest(username)}' if username else None


def ip_key(client_ip: str) -> str:
    return f'ip:{_digest(client_ip or "unknown")}'


def _limits() -> tuple[int, int, int, int]:
    return (
        max(1, settings.LOGIN_RATE_LIMIT_FAILURES),
        max(1, settings.LOGIN_RATE_LIMIT_IP_FAILURES),
        max(1, settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS),
        max(1, settings.LOGIN_RATE_LIMIT_LOCKOUT_SECONDS),
    )


def _state_is_blocked(state: LoginThrottleState | None, now) -> bool:
    if not state or not state.blocked_until:
        return False
    return state.blocked_until > now


def is_blocked(*, username: str, client_ip: str) -> bool:
    """检查账号或 IP 是否仍在锁定期内。"""

    keys = [key for key in (account_key(username), ip_key(client_ip)) if key]
    now = timezone.now()
    states = LoginThrottleState.objects.filter(key__in=keys)
    return any(_state_is_blocked(state, now) for state in states)


def _record_one(key: str, threshold: int, now, window_seconds: int, lockout_seconds: int):
    with transaction.atomic():
        state, _ = LoginThrottleState.objects.select_for_update().get_or_create(key=key)
        if state.updated_at < now - timedelta(seconds=window_seconds):
            state.failure_count = 0
            state.blocked_until = None
        state.failure_count += 1
        if state.failure_count >= threshold:
            state.blocked_until = now + timedelta(seconds=lockout_seconds)
        state.save(update_fields=['failure_count', 'blocked_until', 'updated_at'])


def record_failure(*, username: str, client_ip: str) -> None:
    """记录一次失败登录，账号和 IP 各自达到阈值后锁定。"""

    account_limit, ip_limit, window_seconds, lockout_seconds = _limits()
    now = timezone.now()
    if username:
        _record_one(account_key(username), account_limit, now, window_seconds, lockout_seconds)
    _record_one(ip_key(client_ip), ip_limit, now, window_seconds, lockout_seconds)


def clear_account_failures(username: str) -> None:
    """成功登录后只清理账号维度，保留 IP 维度用于限制集中攻击。"""

    key = account_key(username)
    if key:
        LoginThrottleState.objects.filter(key=key).delete()
