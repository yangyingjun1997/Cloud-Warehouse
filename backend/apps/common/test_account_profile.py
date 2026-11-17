from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from apps.accounts.models import UserProfile
from apps.common.models import OperationAuditLog


User = get_user_model()


class AccountProfilePageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='profile-user',
            password='password123',
        )
        UserProfile.objects.create(
            user=self.user,
            employee_no='EMP-PROFILE',
        )
        self.client = Client()
        self.client.force_login(self.user)

    def test_profile_page_updates_only_display_name(self):
        response = self.client.post('/account/profile/', {
            'display_name': '  张三  ',
            'username': 'should-not-change',
        })

        self.assertRedirects(response, '/account/profile/')
        self.user.refresh_from_db()
        self.assertEqual(self.user.get_full_name(), '张三')
        self.assertEqual(self.user.username, 'profile-user')
        self.assertTrue(OperationAuditLog.objects.filter(
            action='account_display_name_updated',
            actor=self.user,
        ).exists())

        profile_page = self.client.get('/account/profile/')
        self.assertNotContains(profile_page, 'name="dingtalk_user_id"')
        # 钉钉客户端协议链接无法在 PC 端打开，已移除；绑定状态仍只读展示。
        self.assertNotContains(profile_page, 'dingtalk://')
        self.assertContains(profile_page, '钉钉绑定')

        page = self.client.get('/')
        self.assertContains(page, '张三')
        self.assertContains(page, '个人资料')

    def test_account_fields_are_read_only_and_not_submittable(self):
        """账号、工号、部门、钉钉状态不可由本人提交修改。"""
        response = self.client.post('/account/profile/', {
            'display_name': '李四',
            'username': 'hacked-account',
            'employee_no': 'HACKED-001',
            'department': '',
            'dingtalk_user_id': 'forged-userid',
        })

        self.assertRedirects(response, '/account/profile/')
        self.user.refresh_from_db()
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(self.user.username, 'profile-user')
        self.assertEqual(profile.employee_no, 'EMP-PROFILE')
        self.assertEqual(profile.dingtalk_user_id, '')
        self.assertFalse(profile.is_dingtalk_bound)
        self.assertEqual(self.user.get_full_name(), '李四')

    def test_readonly_fields_render_as_text_not_inputs(self):
        page = self.client.get('/account/profile/')
        self.assertNotContains(page, 'name="username"')
        self.assertNotContains(page, 'name="employee_no"')
        self.assertNotContains(page, 'name="department"')
        # 只读信息以展示列表呈现，不再用看起来可编辑的灰框。
        self.assertContains(page, 'detail-list')
        self.assertContains(page, 'EMP-PROFILE')

    def test_empty_name_falls_back_to_username_and_xss_is_escaped(self):
        self.client.post('/account/profile/', {'display_name': '<script>alert(1)</script>'})
        self.user.refresh_from_db()
        self.assertEqual(self.user.get_full_name(), '<script>alert(1)</script>')
        page = self.client.get('/')
        self.assertContains(page, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(page, '<script>alert(1)</script>')

        self.client.post('/account/profile/', {'display_name': ''})
        self.user.refresh_from_db()
        self.assertEqual(self.user.get_full_name(), '')
        page = self.client.get('/')
        self.assertContains(page, 'profile-user')

    def test_anonymous_user_is_redirected(self):
        anonymous = Client()
        response = anonymous.get('/account/profile/')
        self.assertRedirects(response, '/login/?next=/account/profile/')
