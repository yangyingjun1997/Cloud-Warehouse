"""账号会话登记、限制与主动撤销。"""

from __future__ import annotations

import hashlib
import ipaddress

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils import timezone

from apps.common.auth_throttle import get_client_ip
from apps.common.permissions import (
    can_approve_workflow,
    can_execute_inventory,
    is_system_administrator,
)
from apps.common.security_audit import record_login_event

from .models import AccountSession

User = get_user_model()


def _digest(value: str) -> str:
    return hashlib.sha256((value or '').encode('utf-8')).hexdigest()


def masked_ip(value: str) -> str:
    """保留排障所需的网段提示，不展示完整客户端地址。"""

    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return '未知地址'
    if address.version == 4:
        parts = str(address).split('.')
        return '.'.join([*parts[:3], '*'])
    parts = address.exploded.split(':')
    return ':'.join([*parts[:4], '*'])


def device_label(user_agent: str) -> str:
    value = user_agent or ''
    system = next((label for marker, label in (
        ('Windows', 'Windows'),
        ('Android', 'Android'),
        ('iPhone', 'iPhone'),
        ('iPad', 'iPad'),
        ('Macintosh', 'macOS'),
        ('Linux', 'Linux'),
    ) if marker in value), '未知系统')
    browser = next((label for marker, label in (
        ('Edg/', 'Edge'),
        ('Chrome/', 'Chrome'),
        ('Firefox/', 'Firefox'),
        ('Safari/', 'Safari'),
    ) if marker in value), '浏览器')
    return f'{system} · {browser}'


def is_privileged_session_user(user) -> bool:
    return bool(
        is_system_administrator(user)
        or can_approve_workflow(user)
        or can_execute_inventory(user)
    )


def active_account_sessions(user):
    now = timezone.now()
    active_keys = Session.objects.filter(expire_date__gt=now).values('session_key')
    return AccountSession.objects.filter(
        user=user,
        revoked_at__isnull=True,
        session_key__in=active_keys,
    ).order_by('-last_seen_at')


def _revoke_rows(rows, *, reason: str) -> int:
    rows = list(rows)
    if not rows:
        return 0
    Session.objects.filter(session_key__in=[row.session_key for row in rows]).delete()
    now = timezone.now()
    AccountSession.objects.filter(pk__in=[row.pk for row in rows]).update(
        revoked_at=now,
        revoked_reason=reason[:120],
        updated_at=now,
    )
    return len(rows)


@transaction.atomic
def register_account_session(request, user=None) -> AccountSession | None:
    user = user or getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return None
    if not request.session.session_key:
        request.session.save()
    session_key = request.session.session_key
    client_ip = get_client_ip(request)
    user_agent = request.META.get('HTTP_USER_AGENT', '')[:1000]
    now = timezone.now()

    locked_user = User.objects.select_for_update().get(pk=user.pk)
    account_session, _ = AccountSession.objects.update_or_create(
        session_key=session_key,
        defaults={
            'user': locked_user,
            'ip_hash': _digest(client_ip or 'unknown'),
            'ip_hint': masked_ip(client_ip),
            'user_agent_hash': _digest(user_agent) if user_agent else '',
            'device_label': device_label(user_agent),
            'last_seen_at': now,
            'revoked_at': None,
            'revoked_reason': '',
        },
    )

    limit = max(0, int(getattr(settings, 'PRIVILEGED_MAX_ACTIVE_SESSIONS', 2)))
    revoked_count = 0
    if limit and is_privileged_session_user(locked_user):
        active = list(active_account_sessions(locked_user))
        revoked_count = _revoke_rows(
            active[limit:],
            reason=f'超过特权账号最多 {limit} 个会话限制',
        )
    if revoked_count:
        record_login_event(
            event_type='session_limit_enforced',
            username=locked_user.get_username(),
            client_ip=client_ip,
            metadata={'revoked_count': revoked_count, 'limit': limit},
        )
    request.session['_account_session_synced_at'] = int(now.timestamp())
    return account_session


@transaction.atomic
def revoke_account_session(*, actor, account_session, client_ip: str, reason='用户主动退出设备') -> None:
    account_session = AccountSession.objects.select_for_update().get(pk=account_session.pk)
    if account_session.user_id != actor.id:
        raise AccountSession.DoesNotExist
    _revoke_rows([account_session], reason=reason)
    record_login_event(
        event_type='session_revoked',
        username=actor.get_username(),
        client_ip=client_ip,
        metadata={
            'target_session_hash': _digest(account_session.session_key),
            'reason': reason,
        },
    )


@transaction.atomic
def revoke_other_account_sessions(*, request, actor) -> int:
    current_key = request.session.session_key
    rows = list(active_account_sessions(actor).exclude(session_key=current_key))
    revoked_count = _revoke_rows(rows, reason='用户主动退出其他设备')
    if revoked_count:
        record_login_event(
            event_type='session_others_revoked',
            username=actor.get_username(),
            client_ip=get_client_ip(request),
            metadata={'revoked_count': revoked_count},
        )
    return revoked_count


def mark_current_session_revoked(request, *, reason='用户退出登录') -> None:
    if not request.session.session_key:
        return
    now = timezone.now()
    AccountSession.objects.filter(
        user=request.user,
        session_key=request.session.session_key,
        revoked_at__isnull=True,
    ).update(revoked_at=now, revoked_reason=reason, updated_at=now)
