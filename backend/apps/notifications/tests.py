from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.test import override_settings
from rest_framework.test import APIClient
from unittest.mock import patch
from datetime import timedelta

from django.utils import timezone

from apps.inventory.models import Asset, Category, ItemType, StockItem
from apps.common.models import OperationAuditLog
from apps.workflow.models import InventoryReservation, StockItemLoan, WorkflowRequest

from .low_stock import (
    apply_snapshot,
    item_type_snapshot,
    send_weekly_summary,
    stock_item_snapshot,
)
from .models import LowStockAlert, Notification
from .return_reminders import send_return_due_reminders
from .dingtalk import send_user_message


User = get_user_model()


class NotificationApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='notice-user', password='password123')
        self.other_user = User.objects.create_user(username='other-user', password='password123', is_staff=True)
        self.notification = Notification.objects.create(
            recipient=self.user,
            title='待处理申请',
            content='请处理一条出库申请。',
        )
        Notification.objects.create(recipient=self.other_user, title='其他通知', content='不应被读取。')
        self.client = APIClient()

    def test_notifications_are_scoped_to_current_user(self):
        self.client.force_authenticate(self.other_user)
        response = self.client.get('/api/notifications/notifications/unread-count/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)

        response = self.client.get(f'/api/notifications/notifications/{self.notification.id}/')
        self.assertEqual(response.status_code, 404)

    @override_settings(DINGTALK_APP_KEY='', DINGTALK_APP_SECRET='', DINGTALK_AGENT_ID='')
    def test_personal_dingtalk_reports_unconfigured_state(self):
        result = send_user_message(self.user, '测试通知', '测试内容')

        self.assertFalse(result.delivered)
        self.assertEqual(result.channel, 'dingtalk_personal')
        self.assertIn('未配置', result.reason)


class LowStockNotificationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username='stock-admin', password='password123', email='')
        self.report_user = User.objects.create_user(username='stock-report', password='password123')
        self.report_user.groups.add(Group.objects.get_or_create(name='warehouse_reports')[0])
        self.category = Category.objects.create(name='预警测试分类', code='ALERT-CAT')

    @patch('apps.notifications.low_stock.send_group_message', return_value=True)
    def test_stock_item_alerts_once_and_notifies_when_recovered(self, send_group_message):
        stock_item = StockItem.objects.create(
            name='测试滤芯', code='ALERT-STOCK', quantity=5, safety_stock=5, unit='件',
        )
        with self.captureOnCommitCallbacks(execute=True):
            apply_snapshot(stock_item_snapshot(stock_item.id))

        alert = LowStockAlert.objects.get(source_id=stock_item.id)
        self.assertTrue(alert.is_active)
        self.assertEqual(send_group_message.call_count, 1)
        self.assertEqual(Notification.objects.filter(title='安全库存预警').count(), 2)
        self.assertTrue(OperationAuditLog.objects.filter(
            action='low_stock_alert_started', target_id=alert.id,
        ).exists())

        with self.captureOnCommitCallbacks(execute=True):
            apply_snapshot(stock_item_snapshot(stock_item.id))
        self.assertEqual(send_group_message.call_count, 1)

        StockItem.objects.filter(pk=stock_item.id).update(quantity=6)
        with self.captureOnCommitCallbacks(execute=True):
            apply_snapshot(stock_item_snapshot(stock_item.id))
        alert.refresh_from_db()
        self.assertFalse(alert.is_active)
        self.assertIsNotNone(alert.resolved_at)
        self.assertEqual(send_group_message.call_count, 2)
        self.assertEqual(Notification.objects.filter(title='安全库存已恢复').count(), 2)
        self.assertTrue(OperationAuditLog.objects.filter(
            action='low_stock_alert_resolved', target_id=alert.id,
        ).exists())

    def test_serialized_assets_are_counted_by_item_type(self):
        item_type = ItemType.objects.create(
            name='测试机器狗', code='ALERT-ASSET-TYPE', category=self.category,
            is_serialized=True, safety_stock=2, unit='台',
        )
        Asset.objects.create(name='机器狗一号', item_type=item_type, status=Asset.Status.IN_STOCK)
        Asset.objects.create(name='机器狗二号', item_type=item_type, status=Asset.Status.BORROWED)

        snapshot = item_type_snapshot(item_type.id)
        self.assertEqual(snapshot.current_quantity, 1)
        self.assertEqual(snapshot.safety_stock, 2)
        self.assertTrue(snapshot.is_low)

    def test_active_reservation_reduces_available_safety_stock(self):
        item_type = ItemType.objects.create(
            name='预约资产类型', code='RESERVED-ASSET-TYPE', category=self.category,
            is_serialized=True, safety_stock=1, unit='台',
        )
        asset = Asset.objects.create(name='预约测试资产', item_type=item_type, status=Asset.Status.IN_STOCK)
        request_obj = WorkflowRequest.objects.create(
            applicant=self.report_user,
            request_type=WorkflowRequest.RequestType.BORROW,
            usage_location='测试区',
            status=WorkflowRequest.Status.PENDING,
        )
        line = request_obj.lines.create(asset=asset, quantity=1)
        InventoryReservation.objects.create(request_line=line, asset=asset, quantity=1)

        snapshot = item_type_snapshot(item_type.id)

        self.assertEqual(snapshot.current_quantity, 0)
        self.assertTrue(snapshot.is_low)

    @patch('apps.notifications.low_stock.send_group_message', return_value=True)
    def test_weekly_summary_contains_active_alerts(self, send_group_message):
        stock_item = StockItem.objects.create(
            name='测试电池', code='ALERT-WEEKLY', quantity=1, safety_stock=3, unit='块',
        )
        apply_snapshot(stock_item_snapshot(stock_item.id), notify=False)

        count = send_weekly_summary()

        self.assertEqual(count, 1)
        send_group_message.assert_called_once()
        self.assertIn('每周安全库存汇总', send_group_message.call_args.args[0])
        self.assertIn('测试电池', send_group_message.call_args.args[1])


class ReturnReminderTests(TestCase):
    def setUp(self):
        self.borrower = User.objects.create_user(username='borrower', password='password123')
        self.operator = User.objects.create_user(username='reminder-operator', password='password123')
        self.operator.groups.add(Group.objects.get_or_create(name='warehouse_staff')[0])
        self.stock_item = StockItem.objects.create(name='借用测试线缆', code='LOAN-CABLE', unit='条', quantity=8)
        self.request_obj = WorkflowRequest.objects.create(
            applicant=self.borrower,
            request_type=WorkflowRequest.RequestType.BORROW,
            status=WorkflowRequest.Status.DONE,
            expected_return_date=timezone.localdate() + timedelta(days=2),
            usage_location='测试区',
        )
        self.line = self.request_obj.lines.create(stock_item=self.stock_item, quantity=2)
        StockItemLoan.objects.create(
            borrower=self.borrower,
            stock_item=self.stock_item,
            source_line=self.line,
            original_quantity=2,
            outstanding_quantity=2,
            expected_return_date=self.request_obj.expected_return_date,
        )

    @patch('apps.notifications.return_reminders.send_group_message', return_value=True)
    def test_due_reminder_is_sent_once_per_day(self, send_group_message):
        with self.captureOnCommitCallbacks(execute=True):
            first_count = send_return_due_reminders()
        with self.captureOnCommitCallbacks(execute=True):
            second_count = send_return_due_reminders()

        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 0)
        self.assertEqual(Notification.objects.filter(recipient=self.borrower, title='借用归还提醒').count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.operator, title='借用到期汇总').count(), 1)
        send_group_message.assert_called_once()
