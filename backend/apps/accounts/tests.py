from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings
from rest_framework.test import APIClient

from apps.common.models import SecurityAuditEvent

from .models import AccountSession, UserProfile

User = get_user_model()


class MeApiTests(TestCase):
    def test_authenticated_user_can_read_me(self):
        user = User.objects.create_user(username='employee', password='password123')
        client = APIClient()
        client.force_authenticate(user)

        response = client.get('/api/accounts/me/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['username'], 'employee')
        self.assertFalse(response.data['is_staff'])
        self.assertFalse(response.data['is_warehouse_operator'])

    def test_regular_employee_cannot_manage_departments_or_profiles(self):
        user = User.objects.create_user(username='employee', password='password123')
        client = APIClient()
        client.force_authenticate(user)

        department_response = client.get('/api/accounts/departments/')
        profile_response = client.get('/api/accounts/profiles/')

        self.assertEqual(department_response.status_code, 403)
        self.assertEqual(profile_response.status_code, 403)

    def test_system_administrator_can_manage_departments_and_profiles(self):
        admin = User.objects.create_superuser(
            username='system-admin',
            password='password123',
            email='',
        )
        profile_user = User.objects.create_user(username='profile-user', password='password123')
        UserProfile.objects.create(user=profile_user, employee_no='EMP-PROFILE')
        client = APIClient()
        client.force_authenticate(admin)

        department_response = client.post('/api/accounts/departments/', {
            'name': '售后部门',
            'code': 'AFTER-SALES',
        }, format='json')

        self.assertEqual(department_response.status_code, 201)
        profile_response = client.get('/api/accounts/profiles/')

        self.assertEqual(profile_response.status_code, 200)
        self.assertTrue(any(
            row['username'] == profile_user.username
            for row in profile_response.data['results']
        ))


@override_settings(
    PRIVILEGED_MAX_ACTIVE_SESSIONS=2,
    ACCOUNT_SESSION_SYNC_SECONDS=0,
)
class AccountSessionTests(TestCase):
    def _login(self, user, *, user_agent='Mozilla/5.0 (Windows NT 10.0) Chrome/120'):
        client = Client()
        response = client.post(
            '/login/',
            {'username': user.username, 'password': 'password123'},
            REMOTE_ADDR='192.168.23.88',
            HTTP_USER_AGENT=user_agent,
        )
        self.assertRedirects(response, '/')
        return client, client.session.session_key

    def test_regular_user_can_keep_multiple_devices(self):
        user = User.objects.create_user(username='multi-device-user', password='password123')

        logins = [self._login(user) for _ in range(3)]

        self.assertEqual(AccountSession.objects.filter(user=user, revoked_at=None).count(), 3)
        for client, _ in logins:
            self.assertEqual(client.get('/').status_code, 200)

    def test_privileged_user_keeps_only_two_newest_sessions(self):
        user = User.objects.create_user(username='limited-user', password='password123')
        user.groups.add(Group.objects.get_or_create(name='warehouse_outbound')[0])

        first, first_key = self._login(user, user_agent='Mozilla/5.0 (Windows NT 10.0) Edg/120')
        second, _ = self._login(user, user_agent='Mozilla/5.0 (Android) Chrome/120')
        third, _ = self._login(user, user_agent='Mozilla/5.0 (iPhone) Safari/17')

        self.assertRedirects(first.get('/'), '/login/?next=/', fetch_redirect_response=False)
        self.assertEqual(second.get('/').status_code, 200)
        self.assertEqual(third.get('/').status_code, 200)
        self.assertEqual(AccountSession.objects.filter(user=user, revoked_at=None).count(), 2)
        revoked = AccountSession.objects.get(session_key=first_key)
        self.assertIsNotNone(revoked.revoked_at)
        self.assertIn('最多 2 个会话', revoked.revoked_reason)
        self.assertTrue(
            SecurityAuditEvent.objects.filter(event_type='session_limit_enforced').exists()
        )

    def test_user_can_revoke_another_device_but_not_current_device(self):
        user = User.objects.create_user(username='session-owner', password='password123')
        first, first_key = self._login(user)
        second, second_key = self._login(user, user_agent='Mozilla/5.0 (Android) Chrome/120')
        first_session = AccountSession.objects.get(session_key=first_key)
        second_session = AccountSession.objects.get(session_key=second_key)

        page = second.get('/account/sessions/')
        self.assertContains(page, '登录设备')
        self.assertContains(page, '192.168.23.*')
        self.assertNotContains(page, '192.168.23.88')
        self.assertNotContains(page, first_key)
        self.assertNotContains(page, second_key)

        response = second.post('/account/sessions/', {
            'action': 'revoke',
            'session_id': str(first_session.pk),
        })

        self.assertRedirects(response, '/account/sessions/')
        self.assertRedirects(first.get('/'), '/login/?next=/', fetch_redirect_response=False)
        self.assertEqual(second.get('/').status_code, 200)
        first_session.refresh_from_db()
        self.assertIsNotNone(first_session.revoked_at)
        self.assertTrue(SecurityAuditEvent.objects.filter(event_type='session_revoked').exists())

        response = second.post('/account/sessions/', {
            'action': 'revoke',
            'session_id': str(second_session.pk),
        })
        self.assertRedirects(response, '/account/sessions/')
        self.assertEqual(second.get('/').status_code, 200)

    def test_user_cannot_revoke_another_accounts_session(self):
        owner = User.objects.create_user(username='owner', password='password123')
        attacker = User.objects.create_user(username='attacker', password='password123')
        _, owner_key = self._login(owner)
        attacker_client, _ = self._login(attacker)
        target = AccountSession.objects.get(session_key=owner_key)

        response = attacker_client.post('/account/sessions/', {
            'action': 'revoke',
            'session_id': str(target.pk),
        })

        self.assertEqual(response.status_code, 404)
        target.refresh_from_db()
        self.assertIsNone(target.revoked_at)
