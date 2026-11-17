from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.common.models import SecurityAuditEvent

User = get_user_model()


class DisabledAccountLoginTests(TestCase):
    """停用账号登录时应给出明确提示，但不能泄露账号是否存在。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username='disabled-user', password='correct-pass-123',
        )
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])

    def test_disabled_account_with_correct_password_shows_specific_message(self):
        response = self.client.post('/login/', {
            'username': 'disabled-user',
            'password': 'correct-pass-123',
        })

        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('已停用', content)

    def test_wrong_password_on_disabled_account_stays_generic(self):
        """密码不对时不能透露账号被停用，否则等于确认账号存在。"""
        response = self.client.post('/login/', {
            'username': 'disabled-user',
            'password': 'wrong-password',
        })

        content = response.content.decode('utf-8')
        self.assertNotIn('已停用', content)

    def test_nonexistent_account_stays_generic(self):
        response = self.client.post('/login/', {
            'username': 'no-such-user',
            'password': 'whatever',
        })
        content = response.content.decode('utf-8')
        self.assertNotIn('已停用', content)

    def test_active_account_can_still_login(self):
        active = User.objects.create_user(
            username='active-user', password='correct-pass-123',
        )
        response = self.client.post('/login/', {
            'username': 'active-user',
            'password': 'correct-pass-123',
        })
        self.assertEqual(response.status_code, 302)
