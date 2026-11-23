"""P2-16 回归：账号申请的敏感字段不得经钉钉外发。

背景：account_notifications 曾把「联系电话」和「申请说明」原样拼进钉钉消息。
手机号属于个人敏感信息，申请人填写时并未预期它被转发到第三方 IM 或群会话。
修复：站内通知保留完整信息（需登录且有审批权限才能看到），
钉钉只发概要，不含手机号与自由文本说明。
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.accounts.account_notifications import notify_account_request_submitted
from apps.notifications.models import Notification

User = get_user_model()


class AccountRequestNotificationPrivacyTests(TestCase):
    def setUp(self):
        self.reviewer = User.objects.create_user(
            username='p216_reviewer', password='Pw#2026aa',
        )
        self.reviewer.groups.add(
            Group.objects.get_or_create(name='warehouse_account_review')[0]
        )
        self.applicant = User.objects.create_user(
            username='p216_applicant', password='Pw#2026aa',
        )

        from apps.accounts.models import AccountRequest

        self.request_obj = AccountRequest.objects.create(
            real_name='敏感测试员',
            phone='13900001234',
            reason='团队内部调动，需要开通仓库账号用于日常出入库登记',
            department_text='售后部',
            status=AccountRequest.Status.PENDING,
        )

    def _notify(self):
        captured = {}

        def fake_enqueue(users, title, content, **kwargs):
            captured['users'] = list(users)
            captured['title'] = title
            captured['content'] = content
            captured['kwargs'] = kwargs

        with patch(
            'apps.accounts.account_notifications.enqueue_users_or_group',
            side_effect=fake_enqueue,
        ):
            count = notify_account_request_submitted(
                self.request_obj, requested_by=self.applicant,
            )
        return count, captured

    def test_dingtalk_does_not_contain_phone(self):
        count, captured = self._notify()
        self.assertGreater(count, 0)
        self.assertNotIn('13900001234', captured['content'],
                         '钉钉消息不应包含申请手机号')

    def test_dingtalk_does_not_contain_reason_text(self):
        _count, captured = self._notify()
        self.assertNotIn('团队内部调动', captured['content'],
                         '钉钉消息不应包含自由文本申请说明')

    def test_dingtalk_still_identifies_applicant_and_action(self):
        _count, captured = self._notify()
        self.assertIn('敏感测试员', captured['content'],
                      '钉钉消息应保留申请人姓名，否则审批人不知是谁申请')
        self.assertIn('账号与权限', captured['content'],
                      '钉钉消息应引导审批人到系统内查看详情')

    def test_in_app_notification_keeps_full_information(self):
        """站内通知需登录且有权限才能看到，应保留完整信息供审批判断。"""
        self._notify()
        note = Notification.objects.filter(recipient=self.reviewer).first()
        self.assertIsNotNone(note)
        self.assertIn('13900001234', note.content, '站内通知应保留手机号')
        self.assertIn('团队内部调动', note.content, '站内通知应保留申请说明')
