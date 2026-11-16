from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from .dingtalk import send_group_message, send_user_message, send_users_or_group
from .models import DingTalkOutbox


logger = logging.getLogger(__name__)
User = get_user_model()


def outbox_key(*parts) -> str:
    payload = '\x1f'.join(str(part) for part in parts)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def enqueue_users_or_group(users, title: str, content: str, *, idempotency_key: str):
    user_ids = sorted({str(user.pk) for user in users if user and user.is_active})
    return DingTalkOutbox.objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults={
            'delivery_mode': DingTalkOutbox.DeliveryMode.USERS_OR_GROUP,
            'recipient_user_ids': user_ids,
            'title': title,
            'content': content,
            'next_attempt_at': timezone.now(),
        },
    )


def enqueue_personal(users, title: str, content: str, *, idempotency_key: str):
    user_ids = sorted({str(user.pk) for user in users if user and user.is_active})
    return DingTalkOutbox.objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults={
            'delivery_mode': DingTalkOutbox.DeliveryMode.PERSONAL,
            'recipient_user_ids': user_ids,
            'title': title,
            'content': content,
            'next_attempt_at': timezone.now(),
        },
    )


def enqueue_group(title: str, content: str, *, idempotency_key: str):
    return DingTalkOutbox.objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults={
            'delivery_mode': DingTalkOutbox.DeliveryMode.GROUP,
            'title': title,
            'content': content,
            'next_attempt_at': timezone.now(),
        },
    )


def _claim_due(limit: int, *, now, lock_timeout: timedelta):
    stale_before = now - lock_timeout
    DingTalkOutbox.objects.filter(
        status=DingTalkOutbox.Status.SENDING,
        locked_at__lt=stale_before,
    ).update(
        status=DingTalkOutbox.Status.PENDING,
        locked_at=None,
        next_attempt_at=now,
        last_error='上次发送进程未正常结束，已重新入队。',
    )
    with transaction.atomic():
        queryset = DingTalkOutbox.objects.filter(
            status=DingTalkOutbox.Status.PENDING,
            next_attempt_at__lte=now,
        ).order_by('next_attempt_at', 'created_at')
        if connection.features.has_select_for_update_skip_locked:
            queryset = queryset.select_for_update(skip_locked=True)
        else:
            queryset = queryset.select_for_update()
        rows = list(queryset[:limit])
        for row in rows:
            row.status = DingTalkOutbox.Status.SENDING
            row.locked_at = now
            row.attempt_count += 1
            row.save(update_fields=['status', 'locked_at', 'attempt_count', 'updated_at'])
    return rows


def _deliver(row: DingTalkOutbox) -> tuple[bool, str]:
    if row.delivery_mode == DingTalkOutbox.DeliveryMode.GROUP:
        delivered = send_group_message(row.title, row.content)
        return delivered, '' if delivered else '群机器人未配置或发送失败'

    users = list(User.objects.filter(pk__in=row.recipient_user_ids, is_active=True))
    if row.delivery_mode == DingTalkOutbox.DeliveryMode.PERSONAL:
        results = [send_user_message(user, row.title, row.content) for user in users]
        delivered = bool(results) and all(result.delivered for result in results)
        reasons = sorted({result.reason for result in results if not result.delivered and result.reason})
        return delivered, '；'.join(reasons) or ('未找到可送达的接收人' if not delivered else '')

    results = send_users_or_group(users, row.title, row.content)
    group_delivered = any(result.channel == 'dingtalk_group' and result.delivered for result in results)
    personal_results = [result for result in results if result.channel == 'dingtalk_personal']
    delivered = group_delivered or bool(personal_results and all(result.delivered for result in personal_results))
    reasons = sorted({result.reason for result in results if not result.delivered and result.reason})
    return delivered, '；'.join(reasons) or ('未找到可送达的接收人' if not delivered else '')


def process_due_outbox(*, limit=100, max_attempts=8, now=None, lock_timeout_minutes=10):
    now = now or timezone.now()
    rows = _claim_due(
        max(int(limit), 1),
        now=now,
        lock_timeout=timedelta(minutes=max(int(lock_timeout_minutes), 1)),
    )
    delivered_count = failed_count = retry_count = 0
    for row in rows:
        try:
            delivered, reason = _deliver(row)
        except Exception as exc:
            logger.exception('Unexpected DingTalk outbox delivery failure for %s', row.pk)
            delivered, reason = False, f'{type(exc).__name__}: {exc}'

        row.locked_at = None
        if delivered:
            row.status = DingTalkOutbox.Status.DELIVERED
            row.delivered_at = timezone.now()
            row.last_error = ''
            delivered_count += 1
        elif row.attempt_count >= max(int(max_attempts), 1):
            row.status = DingTalkOutbox.Status.FAILED
            row.last_error = (reason or '发送失败')[:500]
            failed_count += 1
        else:
            row.status = DingTalkOutbox.Status.PENDING
            delay_seconds = min(60 * (2 ** max(row.attempt_count - 1, 0)), 6 * 60 * 60)
            row.next_attempt_at = timezone.now() + timedelta(seconds=delay_seconds)
            row.last_error = (reason or '发送失败')[:500]
            retry_count += 1
        row.save(update_fields=[
            'status', 'locked_at', 'delivered_at', 'last_error', 'next_attempt_at', 'updated_at',
        ])
    return {
        'claimed': len(rows),
        'delivered': delivered_count,
        'retrying': retry_count,
        'failed': failed_count,
    }
