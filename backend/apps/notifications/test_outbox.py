from datetime import timedelta
from unittest.mock import patch

from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from apps.common.testing.fixtures import WarehouseTestFixture

from .models import DingTalkOutbox
from .outbox import enqueue_group, enqueue_users_or_group, outbox_key, process_due_outbox


class DingTalkOutboxTests(WarehouseTestFixture, TestCase):
    def test_enqueue_is_idempotent_and_does_not_call_network(self):
        user = self.make_operator('outbox-operator')
        key = outbox_key('workflow', 'request-1', 'approved')

        with patch('apps.notifications.outbox.send_users_or_group') as sender:
            first, first_created = enqueue_users_or_group(
                [user], '审批通过', '申请已通过。', idempotency_key=key,
            )
            second, second_created = enqueue_users_or_group(
                [user], '审批通过', '申请已通过。', idempotency_key=key,
            )

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(DingTalkOutbox.objects.count(), 1)
        sender.assert_not_called()

    def test_enqueue_rolls_back_with_business_transaction(self):
        key = outbox_key('rollback-test')

        with self.assertRaisesMessage(RuntimeError, '业务回滚'):
            with transaction.atomic():
                enqueue_group('测试', '不应保留', idempotency_key=key)
                raise RuntimeError('业务回滚')

        self.assertFalse(DingTalkOutbox.objects.filter(idempotency_key=key).exists())

    @patch('apps.notifications.outbox.send_group_message', return_value=True)
    def test_worker_marks_successful_message_delivered(self, sender):
        row, _ = enqueue_group('测试送达', '消息内容', idempotency_key=outbox_key('success'))

        result = process_due_outbox()

        row.refresh_from_db()
        self.assertEqual(result['delivered'], 1)
        self.assertEqual(row.status, DingTalkOutbox.Status.DELIVERED)
        self.assertEqual(row.attempt_count, 1)
        self.assertIsNotNone(row.delivered_at)
        # 渠道化为多群预留后，worker 会显式传 channel 参数（默认 'default'）
        sender.assert_called_once_with('测试送达', '消息内容', channel='default')

    @patch('apps.notifications.outbox.send_group_message', return_value=False)
    def test_worker_retries_then_marks_terminal_failure(self, sender):
        row, _ = enqueue_group('测试失败', '消息内容', idempotency_key=outbox_key('failure'))

        first = process_due_outbox(max_attempts=2)
        row.refresh_from_db()
        self.assertEqual(first['retrying'], 1)
        self.assertEqual(row.status, DingTalkOutbox.Status.PENDING)
        self.assertGreater(row.next_attempt_at, timezone.now())

        row.next_attempt_at = timezone.now() - timedelta(seconds=1)
        row.save(update_fields=['next_attempt_at', 'updated_at'])
        second = process_due_outbox(max_attempts=2)
        row.refresh_from_db()
        self.assertEqual(second['failed'], 1)
        self.assertEqual(row.status, DingTalkOutbox.Status.FAILED)
        self.assertEqual(row.attempt_count, 2)
        self.assertTrue(row.last_error)
        self.assertEqual(sender.call_count, 2)

    @patch('apps.notifications.outbox.send_group_message', return_value=True)
    def test_worker_recovers_stale_claim(self, sender):
        row, _ = enqueue_group('测试恢复', '消息内容', idempotency_key=outbox_key('stale'))
        DingTalkOutbox.objects.filter(pk=row.pk).update(
            status=DingTalkOutbox.Status.SENDING,
            locked_at=timezone.now() - timedelta(minutes=30),
        )

        result = process_due_outbox(lock_timeout_minutes=10)

        row.refresh_from_db()
        self.assertEqual(result['delivered'], 1)
        self.assertEqual(row.status, DingTalkOutbox.Status.DELIVERED)
