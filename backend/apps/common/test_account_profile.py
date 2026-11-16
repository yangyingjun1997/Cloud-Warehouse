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

        page = self.client.get('/')
        self.assertContains(page, '张三')
        self.assertContains(page, '个人资料')

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
