"""API 访问令牌的生成、轮换和撤销。"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import ApiAccessToken


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def issue_token(user, ttl_seconds: int | None = None) -> tuple[ApiAccessToken, str]:
    """创建令牌；数据库只保存哈希，原文仅返回给当前调用者一次。

    ``ttl_seconds`` 传 0 表示长期令牌（``expires_at`` 留空，永不自然过期），
    只用于固定在岗设备这类无法自动换凭证的场景。默认仍按
    ``settings.API_TOKEN_TTL_SECONDS`` 签发普通令牌。
    """

    raw_token = secrets.token_urlsafe(48)
    if ttl_seconds is None:
        ttl_seconds = max(60, settings.API_TOKEN_TTL_SECONDS)
    expires_at = None
    if ttl_seconds > 0:
        expires_at = timezone.now() + timedelta(seconds=ttl_seconds)
    token = ApiAccessToken.objects.create(
        user=user,
        token_hash=hash_token(raw_token),
        token_prefix=raw_token[:12],
        expires_at=expires_at,
    )
    return token, raw_token


def rotate_token(token: ApiAccessToken) -> tuple[ApiAccessToken, str]:
    """原子地撤销当前令牌并签发新令牌。"""

    with transaction.atomic():
        current = ApiAccessToken.objects.select_for_update().get(pk=token.pk)
        if not current.is_active:
            raise ValueError('当前令牌已失效。')
        new_token, raw_token = issue_token(current.user)
        current.revoked_at = timezone.now()
        current.replaced_by = new_token
        current.save(update_fields=['revoked_at', 'replaced_by', 'updated_at'])
        return new_token, raw_token


def revoke_token(token: ApiAccessToken) -> None:
    with transaction.atomic():
        current = ApiAccessToken.objects.select_for_update().get(pk=token.pk)
        if current.revoked_at is None:
            current.revoked_at = timezone.now()
            current.save(update_fields=['revoked_at', 'updated_at'])
