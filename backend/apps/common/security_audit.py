"""安全事件审计，禁止记录明文账号、IP、密码或令牌。"""

from __future__ import annotations

from .auth_throttle import _digest
from .models import SecurityAuditEvent


def record_login_event(*, event_type: str, username: str, client_ip: str, metadata=None):
    return SecurityAuditEvent.objects.create(
        event_type=event_type,
        subject_hash=_digest(username) if username else '',
        ip_hash=_digest(client_ip or 'unknown'),
        metadata=metadata or {},
    )
